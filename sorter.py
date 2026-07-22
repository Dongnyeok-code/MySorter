import os
import shutil
import subprocess
import json
import time
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
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

TARGET_DIR = "./documents"        # 정리할 문서 폴더
PROGRESS_FILE = "./progress.json" # 중간 진행상황 저장 파일
BATCH_SIZE = 20                   # Gemini API 호출 묶음 크기
SNIPPET_LENGTH = 1000             # 본문 추출 길이
MODEL_NAME = "gemini-3.1-flash-lite" # 사용 모델

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTIONS = """
너는 초등학교 교실 및 학교 행정 문서를 정리하는 자동 분류 AI야.
제공되는 문서의 제목과 내용을 분석하여, 가장 적합한 [상세 상대 경로]를 결정해줘.

[상세 분류 규칙]
1. 01_학급운영_학생지도 (01-1_학급기초, 01-2_상담_생활지도, 01-3_체험학습_출결, 01-4_가정통신문_알림장)
2. 02-1_수업자료_학습지 ([학년]/[과목]/[단원]/[차시] 경로 생성)
3. 02-2_교육과정_진도 (예: 02-2_교육과정_진도/3학년)
4. 02-3_평가_성적 (예: 02-3_평가_성적/6학년)
5. 03_담당업무_예산_행정 (업무 주제별 자율 폴더 생성)
6. 04_복무_연수_인사 (04-1_복무_출장, 04-2_연수_자기계발, 04-3_성과급_인사)
7. 99_기타문서

[응답 출력 형식]
JSON 형식으로만 답변할 것.
{
  "파일명1.hwpx": "상대폴더경로",
  "파일명2.pdf": "상대폴더경로"
}
"""

# ==========================================
# 2. 유틸리티 및 헬퍼 함수
# ==========================================
def sanitize_rel_path(rel_path: str) -> str:
    """윈도우 및 OS 경로 금지 특수문자 정제"""
    if not rel_path:
        return "99_기타문서"
    parts = re.split(r'[\\/]+', rel_path.strip())
    clean_parts = [re.sub(r'[<>:"/\\|?*]', '_', p).strip() for p in parts if re.sub(r'[<>:"/\\|?*]', '_', p).strip()]
    return os.path.join(*clean_parts) if clean_parts else "99_기타문서"

def clean_json_response(text: str) -> str:
    """Gemini 응답의 백틱 마크다운 제거"""
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

def parse_with_kordoc(file_path: str) -> str:
    try:
        res = subprocess.run(
            ["npx", "-y", "kordoc", file_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True
        )
        return res.stdout
    except Exception:
        return ""

def worker_parse_file(args: tuple) -> tuple:
    rel_path, target_dir, snippet_len = args
    file_path = os.path.join(target_dir, rel_path)
    raw_text = parse_with_kordoc(file_path)
    snippet = raw_text[:snippet_len].strip() if raw_text else "(내용 추출 불가)"
    return rel_path, snippet

def classify_batch_with_retry(batch_items: list, max_retries=3) -> dict:
    prompt = SYSTEM_INSTRUCTIONS + "\n\n[분류할 문서 목록]\n"
    for item in batch_items:
        prompt += f"\n--- 파일명: {item['filename']} ---\n{item['snippet']}\n"

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
            time.sleep(3 * (attempt + 1))

    return {}

# ==========================================
# 3. 메인 실행 로직
# ==========================================
def main():
    if not os.path.exists(TARGET_DIR):
        print(f"❌ 경로를 찾을 수 없습니다: {TARGET_DIR}")
        return

    supported_exts = ('.hwp', '.hwpx', '.pdf', '.docx', '.pptx', '.xlsx')
    
    # 💡 하위 폴더의 모든 파일을 깊이 상관없이 재귀적으로 검색 (os.walk)
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

    print(f"🔍 하위 폴더 포함 총 {len(all_rel_files)}개의 문서를 검색했습니다. (모델: {MODEL_NAME})\n")

    classification_results = load_progress()
    already_done = set(classification_results.keys())
    files_to_process = [f for f in all_rel_files if f not in already_done]

    print(f"📊 기존 완료: {len(already_done)}개 | 남은 파일: {len(files_to_process)}개\n")

    if files_to_process:
        # 1단계: 멀티프로세싱 kordoc 병렬 파싱
        num_workers = max(1, (os.cpu_count() or 4) - 1)
        print(f"⚡ CPU 코어 {num_workers}개를 활용하여 문서 파싱을 병렬로 시작합니다...")

        parsed_snippets = {}
        parse_tasks = [(f, TARGET_DIR, SNIPPET_LENGTH) for f in files_to_process]

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_parse_file, task) for task in parse_tasks]
            for future in tqdm(as_completed(futures), total=len(parse_tasks), desc="📖 [1/3] kordoc 문서 병렬 파싱"):
                rel_path, snippet = future.result()
                parsed_snippets[rel_path] = snippet

        # 2단계: Gemini API 배치 분석
        print("\n🤖 Gemini API를 통한 문서 자동 분류를 진행합니다...")
        total_batches = (len(files_to_process) + BATCH_SIZE - 1) // BATCH_SIZE

        with tqdm(total=total_batches, desc="🤖 [2/3] Gemini API 배치 분석") as pbar:
            for i in range(0, len(files_to_process), BATCH_SIZE):
                chunk_files = files_to_process[i:i + BATCH_SIZE]
                batch_items = [
                    {
                        "filename": os.path.basename(f), 
                        "snippet": parsed_snippets.get(f, "(내용 추출 불가)")
                    } 
                    for f in chunk_files
                ]
                batch_mapping = classify_batch_with_retry(batch_items)

                # 키 매칭 안전 처리
                for rel_path in chunk_files:
                    filename = os.path.basename(rel_path)
                    matched_path = "99_기타문서"
                    for k, v in batch_mapping.items():
                        if filename in k or k in filename:
                            matched_path = v
                            break
                    classification_results[rel_path] = sanitize_rel_path(matched_path)

                save_progress(classification_results)
                pbar.update(1)

    # 3단계: 파일 실제 이동
    print("\n📁 분류 결과에 따라 하위 폴더 이동을 진행합니다...")
    for rel_path in tqdm(all_rel_files, desc="📁 [3/3] 파일 최종 이동"):
        file_path = os.path.join(TARGET_DIR, rel_path)
        if not os.path.exists(file_path):
            continue

        filename = os.path.basename(rel_path)
        target_category_dir = classification_results.get(rel_path, "99_기타문서")
        dest_dir = os.path.join(TARGET_DIR, target_category_dir)
        dest_path = os.path.join(dest_dir, filename)

        # 동일한 위치로 이동하려는 경우는 스킵
        if os.path.abspath(file_path) == os.path.abspath(dest_path):
            continue

        os.makedirs(dest_dir, exist_ok=True)

        # 파일명 충돌 방지
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(filename)
            dest_path = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")

        shutil.move(file_path, dest_path)

    print("\n🎉 모든 하위 폴더 파일들의 자동 분류 및 이동이 완수되었습니다!")

if __name__ == "__main__":
    main()
