import os
import shutil
import subprocess
import pandas as pd
from dotenv import load_dotenv
from google import genai

# ==========================================
# 1. 환경 설정 및 초기화
# ==========================================
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("❌ .env 파일에서 GEMINI_API_KEY를 찾을 수 없습니다.")
    exit(1)

TARGET_DIR = "./documents"
LLM_MODEL = "gemini-2.5-flash"

client = genai.Client(api_key=GEMINI_API_KEY)

# 지원 확장자 정의
TEXT_DOC_EXTS = ('.hwp', '.hwpx', '.pdf', '.docx', '.pptx', '.ppt', '.txt', '.csv')
EXCEL_EXTS = ('.xlsx', '.xls')
MEDIA_EXTS = ('.mp4', '.avi', '.mkv', '.mov', '.mp3', '.wav', '.jpg', '.jpeg', '.png', '.zip', '.7z')
ALL_SUPPORTED_EXTS = TEXT_DOC_EXTS + EXCEL_EXTS + MEDIA_EXTS

CATEGORIES = [
    "01_학급운영_학생지도",
    "02-1_수업자료_5학년",
    "02-2_수업자료_6학년",
    "03_담당업무_행정",
    "04_개인자료_기타"
]

# ==========================================
# 2. 초경량 텍스트 추출 함수 (토큰 절감 핵심)
# ==========================================
def extract_snippet(file_path: str) -> str:
    """분류에 필요한 최소한의 텍스트(200~300자)만 초고속으로 추출"""
    ext = os.path.splitext(file_path)[1].lower()

    # 1. 엑셀: 상위 10행만 경량 추출
    if ext in EXCEL_EXTS:
        try:
            df = pd.read_excel(file_path, nrows=10)
            return df.fillna("").to_string(index=False)[:250]
        except Exception:
            return ""

    # 2. 텍스트/CSV: 앞부분 250자만 읽기
    elif ext in ('.txt', '.csv'):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read(250)
        except Exception:
            return ""

    # 3. HWP / PDF / PPT: 타임아웃 5초로 제한하고 앞 300자만 추출
    elif ext in TEXT_DOC_EXTS:
        try:
            res = subprocess.run(
                ["npx", "-y", "kordoc", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            return res.stdout[:300] if res.returncode == 0 else ""
        except Exception:
            return ""

    return ""

# ==========================================
# 3. 경량화된 초고속 AI 분류 함수
# ==========================================
def classify_file(file_path: str, target_dir: str) -> str:
    filename = os.path.basename(file_path)
    rel_path = os.path.relpath(file_path, target_dir)
    parent_folder = os.path.dirname(rel_path) or "최상위"

    snippet = extract_snippet(file_path)

    # 토큰 최소화 초경량 프롬프트
    prompt = f"""
파일명: {filename}
원래 위치: {parent_folder}
본문 힌트: {snippet if snippet.strip() else '없음'}

[카테고리 목록]
- 01_학급운영_학생지도
- 02-1_수업자료_5학년
- 02-2_수업자료_6학년
- 03_담당업무_행정
- 04_개인자료_기타

위 정보만으로 가장 적합한 카테고리 이름 하나만 단답형으로 출력해.
"""

    try:
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt
        )
        res_text = response.text.strip()
        for cat in CATEGORIES:
            if cat in res_text:
                return cat
        return "04_개인자료_기타"
    except Exception:
        return "04_개인자료_기타"

# ==========================================
# 4. 빈 폴더 정리
# ==========================================
def cleanup_empty_folders(target_dir: str):
    print("\n🧹 빈 폴더 정리를 시작합니다...")
    deleted_count = 0
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for d in dirs:
            folder_path = os.path.join(root, d)
            if os.path.basename(folder_path) in CATEGORIES:
                continue
            if not os.listdir(folder_path):
                try:
                    os.rmdir(folder_path)
                    deleted_count += 1
                except Exception:
                    pass
    print(f"✅ 총 {deleted_count}개의 빈 폴더가 정리되었습니다.")

# ==========================================
# 5. 메인 실행 (실시간 진행바 포함)
# ==========================================
def main():
    if not os.path.exists(TARGET_DIR):
        print(f"❌ 경로를 찾을 수 없습니다: {TARGET_DIR}")
        return

    print(f"📂 '{TARGET_DIR}' 폴더 정리 준비 중...")

    target_files = []
    for root, dirs, files in os.walk(TARGET_DIR):
        dirs[:] = [d for d in dirs if d not in CATEGORIES]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ALL_SUPPORTED_EXTS:
                target_files.append(os.path.join(root, f))

    total_files = len(target_files)
    if total_files == 0:
        print("✨ 정리할 대상 파일이 없습니다.")
        return

    print(f"🔍 총 {total_files}개의 파일을 정리합니다.\n")

    moved_count = 0
    for idx, file_path in enumerate(target_files, 1):
        filename = os.path.basename(file_path)
        
        # AI 분류 실행
        category = classify_file(file_path, TARGET_DIR)
        
        # 이동 작업
        dest_folder = os.path.join(TARGET_DIR, category)
        os.makedirs(dest_folder, exist_ok=True)
        dest_path = os.path.join(dest_folder, filename)

        if os.path.exists(dest_path):
            name, ext = os.path.splitext(filename)
            dest_path = os.path.join(dest_folder, f"{name}_dup{ext}")

        shutil.move(file_path, dest_path)
        moved_count += 1

        # 📊 터미널 실시간 진행바 출력
        pct = (idx / total_files) * 100
        print(f"[{idx}/{total_files} ({pct:.0f}%)] 🚚 [{category}] 👈 {filename}")

    print(f"\n🎉 {moved_count}개 파일 정리 완료!")
    cleanup_empty_folders(TARGET_DIR)

if __name__ == "__main__":
    main()
