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
# .env 파일에서 GEMINI_API_KEY 로드
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("❌ .env 파일에서 GEMINI_API_KEY를 찾을 수 없습니다.")
    print("   프로젝트 폴더에 .env 파일을 만들고 'GEMINI_API_KEY=발급받은키'를 입력해 주세요.")
    exit(1)

TARGET_DIR = "./documents"        # 정리할 문서 폴더
PROGRESS_FILE = "./progress.json" # 중간 진행상황 저장 파일
BATCH_SIZE = 20                   # Gemini API 호출 묶음 크기 (토큰 절약 및 속도 최적화)
SNIPPET_LENGTH = 1000             # 본문 추출 길이 (단원/차시 파악에 최적화)
MODEL_NAME = "gemini-3.1-flash-lite" # 초경량/최저비용 모델 지정

# API 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTIONS = """
너는 초등학교 교실 및 학교 행정 문서를 정리하는 자동 분류 AI야.
제공되는 문서의 제목과 내용을 분석하여, 가장 적합한 [상세 상대 경로]를 결정해줘.

[상세 분류 규칙]
1. 01_학급운영_학생지도
   - 01_학급운영_학생지도/01-1_학급기초
   - 01_학급운영_학생지도/01-2_상담_생활지도
   - 01_학급운영_학생지도/01-3_체험학습_출결
   - 01_학급운영_학생지도/01-4_가정통신문_알림장

2. 02-1_수업자료_학습지
   - [학년] / [과목] / [단원] / [차시] 4단계 하위 경로 생성
   - 예시: 02-1_수업자료_학습지/5학년/영어/08단원_How_much_are_the_shoes/01차시

3. 02-2_교육과정_진도 (예: 02-2_교육과정_진도/3학년)
4. 02-3_평가_성적 (예: 02-3_평가_성적/6학년)

5. 03_담당업무_예산_행정
   - 업무 주제별 자율 폴더 생성 (예: 03_담당업무_예산_행정/정보교육, 03_담당업무_예산_행정/회계_품의 등)

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
    """윈도우 및 OS 경로 금지 특수문자(?, :, *, <, >, |, " 등) 정제"""
    if not rel_path:
        return "99_기타문서"
    parts = re.split(r'[\\/]+', rel_path.strip())
    clean_parts = []
    for part in parts:
        cleaned = re.sub(r'[<>:"/\\|?*]', '_', part).strip()
        if cleaned:
            clean_parts.append(cleaned)
    return os.path.join(*clean_parts) if clean_parts else "99_기타문서"

def clean_json_response(text: str) -> str:
    """Gemini 응답의 백틱 마크다운 제거"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()

def load_progress():
    """진행 상황 저장 파일 불러오기"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_progress(progress_data):
    """진행 상황 체크포인트 저장"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, ensure_ascii=False, indent=2)

def parse_with_kordoc(file_path: str) -> str:
    """kordoc CLI 파싱 (인코딩 에러 예방)"""
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
    """멀티프로세싱 전용 작업 함수"""
    filename, target_dir, snippet_len = args
    file_path = os.path.join(target_dir, filename)
    raw_text = parse_with_kordoc(file_path)
    snippet = raw_text[:snippet_len].strip() if raw_text else "(내용 추출 불가)"
    return filename, snippet

def classify_batch_with_retry(batch_items: list, max_retries=3) -> dict:
    """Rate Limit 및 재시도 로직이 포함된 Gemini API 호출"""
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
    all_files = [f for f in os.listdir(TARGET_DIR)
                 if os.path.isfile(os.path.join(TARGET_DIR, f)) and f.lower().endswith(supported_exts)]

    if not all_files:
        print("📂 처리할 문서 파일이 없습니다.")
        return

    print(f"🔍 총 {len(all_files)}개의 문서를 검색했습니다. (모델: {MODEL_NAME})\n")

    classification_results = load_progress()
    already_done = set(classification_results.keys())
    files_to_process = [f for f in all_files if f not in already_done]

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
                filename, snippet = future.result()
                parsed_snippets[filename] = snippet

        # 2단계: Gemini API 배치 분석
        print("\n🤖 Gemini API를 통한 문서 자동 분류를 진행합니다...")
        total_batches = (len(files_to_process) + BATCH_SIZE - 1) // BATCH_SIZE

        with tqdm(total=total_batches, desc="🤖 [2/3] Gemini API 배치 분석") as pbar:
            for i in range(0, len(files_to_process), BATCH_SIZE):
                chunk_files = files_to_process[i:i + BATCH_SIZE]
                batch_items = [{"filename": f, "snippet": parsed_snippets.get(f, "(내용 추출 불가)")} for f in chunk_files]
                batch_mapping = classify_batch_with_retry(batch_items)

                # 키 매칭 안전 처리 (느슨한 매칭)
                for filename in chunk_files:
                    matched_path = "99_기타문서"
                    for k, v in batch_mapping.items():
                        if filename in k or k in filename:
                            matched_path = v
                            break
                    classification_results[filename] = sanitize_rel_path(matched_path)

                save_progress(classification_results)
                pbar.update(1)

    # 3단계: 파일 실제 이동
    print("\n📁 분류 결과에 따라 하위 폴더 이동을 진행합니다...")
    for filename in tqdm(all_files, desc="📁 [3/3] 파일 최종 이동"):
        file_path = os.path.join(TARGET_DIR, filename)
        if not os.path.exists(file_path):
            continue

        rel_path = classification_results.get(filename, "99_기타문서")
        dest_dir = os.path.join(TARGET_DIR, rel_path)
        dest_path = os.path.join(dest_dir, filename)

        if os.path.abspath(file_path) == os.path.abspath(dest_path):
            continue

        os.makedirs(dest_dir, exist_ok=True)

        if os.path.exists(dest_path):
            base, ext = os.path.splitext(filename)
            dest_path = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")

        shutil.move(file_path, dest_path)

    print("\n🎉 모든 작업이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    main()