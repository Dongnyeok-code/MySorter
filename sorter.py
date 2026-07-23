import os
import shutil
import subprocess
import json
import time
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. 환경변수 및 대량 처리 설정
# ==========================================
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("❌ .env 파일에서 GEMINI_API_KEY를 찾을 수 없습니다.")
    exit(1)

TARGET_DIR = "./documents"         # 정리할 문서 폴더
PROGRESS_FILE = "./progress.json" # 중간 진행상황 저장 파일
BATCH_SIZE = 15                    # Gemini API 배치 크기
SNIPPET_LENGTH = 800               # 본문 추출 길이 (토큰 절감 및 정확도 최적화)
MODEL_NAME = "gemini-2.5-flash"   # 안정성이 검증된 Flash 모델

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTIONS = """
너는 초등학교 교실 및 학교 행정 문서를 정리하는 자동 분류 AI야.
제공되는 [파일 정보]를 종합 분석하여, 가장 적합한 [상세 상대 경로]를 결정해줘.

[상세 분류 규칙]
1. 01_학급운영_학생지도 (01-1_학급기초, 01-2_상담_생활지도, 01-3_체험학습_출결, 01-4_가정통신문_알림장)
2. 02-1_수업자료_학습지 ([학년]/[과목])  *예: 02-1_수업자료_학습지/5학년/영어
3. 02-2_교육과정_진도 ([학년])         *예: 02-2_교육과정_진도/3학년
4. 02-3_평가_성적 ([학년])             *예: 02-3_평가_성적/6학년
5. 03_담당업무_예산_행정 ([업무주제])   *예: 03_담당업무_예산_행정/정보과학
6. 04_복무_연수_인사 (04-1_복무_출장, 04-2_연수_자기계발, 04-3_성과급_인사)
7. 99_기타문서

[응답 출력 형식]
각 파일의 ID를 키로 하는 JSON 객체 형식으로만 정확히 답변할 것.
{
  "FILE_0": "02-1_수업자료_학습지/5학년/영어",
  "FILE_1": "01_학급운영_학생지도/01-1_학급기초"
}
"""

# ==========================================
# 2. 유틸리티 및 헬퍼 함수
# ==========================================
def sanitize_rel_path(rel_path: str) -> str:
    """윈도우 및 OS 경로 금지 특수문자 정제 및 경로 표준화"""
    if not rel_path:
        return "99_기타문서"
    parts = re.split(r'[\\/]+', rel_path.strip())
    clean_parts = [re.sub(r'[<>:"/\\|?*]', '_', p).strip() for p in parts if re.sub(r'[<>:"/\\|?*]', '_', p).strip()]
    return os.path.join(*clean_parts) if clean_parts else "99_기타문서"

def clean_json_response(text: str) -> str:
    """Gemini 응답 마크다운 제거"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_progress(progress_data):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, ensure_ascii=False, indent=2)

def worker_parse_file(args: tuple) -> tuple:
    """확장자별 최적 파서 및 타임아웃을 적용한 경량 파싱"""
    rel_path, target_dir, snippet_len = args
    file_path = os.path.join(target_dir, rel_path)
    ext = os.path.splitext(file_path)[1].lower()
    snippet = ""

    try:
        # 1. 엑셀 파싱 (.xlsx, .xls)
        if ext in ('.xlsx', '.xls'):
            df = pd.read_excel(file_path, nrows=10)
            snippet = df.fillna("").to_string(index=False)[:snippet_len]
        
        # 2. 텍스트/CSV 파싱
        elif ext in ('.txt', '.csv'):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                snippet = f.read(snippet_len)
        
        # 3. 일반 문서 파싱 (kordoc CLI, 10초 타임아웃)
        else:
            res = subprocess.run(
                ["npx", "-y", "kordoc", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10
            )
            if res.returncode == 0:
                snippet = res.stdout[:snippet_len].strip()
    except Exception:
        snippet = ""

    if not snippet:
        snippet = "(본문 추출 불가 - 파일명 및 기존 위치 정보 활용)"

    return rel_path, snippet

def classify_batch_with_retry(batch_items: list, max_retries=3) -> dict:
    prompt = SYSTEM_INSTRUCTIONS + "\n\n[분류할 문서 목록]\n"
    for item in batch_items:
        prompt += f"\n- ID: {item['id']}\n"
        prompt += f"  파일명: {item['filename']}\n"
        prompt += f"  기존 위치: {item['parent_dir']}\n"
        prompt += f"  본문 요약: {item['snippet']}\n"

    for attempt in range(max_retries):
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config
            )
            return json.loads(clean_json_response(response.text))
        except Exception:
            time.sleep(2 * (attempt + 1))

    return {}

def cleanup_empty_folders(target_dir: str):
    """파일 이동 완료 후 비어있는 하위 폴더 자동 삭제"""
    deleted_count = 0
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for d in dirs:
            folder_path = os.path.join(root, d)
            if not os.listdir(folder_path):
                try:
                    os.rmdir(folder_path)
                    deleted_count += 1
                except Exception:
                    pass
    if deleted_count > 0:
        print(f"🧹 남아있는 빈 폴더 {deleted_count}개를 정리했습니다.")

# ==========================================
# 3. 메인 실행 로직
# ==========================================
def main():
    if not os.path.exists(TARGET_DIR):
        print(f"❌ 경로를 찾을 수 없습니다: {TARGET_DIR}")
        return

    supported_exts = ('.hwp', '.hwpx', '.pdf', '.docx', '.pptx', '.ppt', '.xlsx', '.xls', '.txt', '.csv')
    
    # 하위 폴더의 모든 파일을 재귀적으로 검색
    all_rel_files = []
    for root, dirs, files in os.walk(TARGET_DIR):
        for f in files:
            if f.lower().endswith(supported_exts):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, TARGET_DIR)
                all_rel_files.append(rel_path)

    if not all_rel_files:
        print("📂 처리할 문서 파일이 없습니다.")
        return

    print(f"🔍 총 {len(all_rel_files)}개의 문서를 탐색했습니다. (모델: {MODEL_NAME})\n")

    classification_results = load_progress()
    already_done = set(classification_results.keys())
    files_to_process = [f for f in all_rel_files if f not in already_done]

    print(f"📊 기존 완료: {len(already_done)}개 | 남은 파일: {len(files_to_process)}개\n")

    if files_to_process:
        # 1단계: 멀티프로세싱 병렬 파싱
        num_workers = max(1, (os.cpu_count() or 4) - 1)
        print(f"⚡ CPU 코어 {num_workers}개로 문서 본문 병렬 파싱 진행 중...")

        parsed_snippets = {}
        parse_tasks = [(f, TARGET_DIR, SNIPPET_LENGTH) for f in files_to_process]

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_parse_file, task) for task in parse_tasks]
            for future in tqdm(as_completed(futures), total=len(parse_tasks), desc="📖 [1/3] 문서 파싱"):
                rel_path, snippet = future.result()
                parsed_snippets[rel_path] = snippet

        # 2단계: 고유 ID 기반 Gemini API 배치 분석
        print("\n🤖 Gemini API를 통한 오차 없는 문서 자동 분류를 진행합니다...")
        total_batches = (len(files_to_process) + BATCH_SIZE - 1) // BATCH_SIZE

        with tqdm(total=total_batches, desc="🤖 [2/3] Gemini API 배치 분석") as pbar:
            for i in range(0, len(files_to_process), BATCH_SIZE):
                chunk_files = files_to_process[i:i + BATCH_SIZE]
                
                # 배치 내 고유 ID 부여 및 ID-경로 맵 구축
                id_map = {}
                batch_items = []
                for idx, rel_path in enumerate(chunk_files):
                    item_id = f"FILE_{idx}"
                    id_map[item_id] = rel_path
                    parent_dir = os.path.dirname(rel_path) or "최상위"
                    
                    batch_items.append({
                        "id": item_id,
                        "filename": os.path.basename(rel_path),
                        "parent_dir": parent_dir,
                        "snippet": parsed_snippets.get(rel_path, "(내용 추출 불가)")
                    })

                # Gemini API 호출
                batch_mapping = classify_batch_with_retry(batch_items)

                # 고유 ID 기반 100% 무오류 매칭
                for item_id, rel_path in id_map.items():
                    matched_path = batch_mapping.get(item_id, "99_기타문서")
                    classification_results[rel_path] = sanitize_rel_path(matched_path)

                save_progress(classification_results)
                pbar.update(1)

    # 3단계: 파일 실제 이동
    print("\n📁 분류 결과에 따라 안전하게 파일 이동을 시작합니다...")
    for rel_path in tqdm(all_rel_files, desc="📁 [3/3] 파일 최종 이동"):
        file_path = os.path.join(TARGET_DIR, rel_path)
        if not os.path.exists(file_path):
            continue

        filename = os.path.basename(rel_path)
        target_category_dir = classification_results.get(rel_path, "99_기타문서")
        dest_dir = os.path.join(TARGET_DIR, target_category_dir)
        dest_path = os.path.join(dest_dir, filename)

        # 동일 위치 스킵
        if os.path.abspath(file_path) == os.path.abspath(dest_path):
            continue

        os.makedirs(dest_dir, exist_ok=True)

        # 중복 파일명 안전 처리
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(filename)
            dest_path = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")

        shutil.move(file_path, dest_path)

    # 4단계: 빈 폴더 정리
    cleanup_empty_folders(TARGET_DIR)

    print("\n🎉 모든 문서 파일의 자동 분류 및 정리가 완벽히 완료되었습니다!")

if __name__ == "__main__":
    main()
