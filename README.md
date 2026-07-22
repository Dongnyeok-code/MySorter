# 📂 MySorter: AI 기반 초등학교 문서 자동 분류 프로그램

> **kordoc**의 HWP/PDF 문서 파싱 능력과 **Gemini 3.1 Flash-Lite** 모델을 결합하여, 수천~수만 개의 학교 행정 및 수업자료를 자동으로 읽고 정돈된 하위 폴더 체계로 분류해 주는 파이썬 프로그램입니다.

---

## 🌟 주요 특징 (Key Features)

- 📄 **HWP, HWPX, PDF 완벽 지원**: 오픈소스 파서인 `kordoc` CLI를 활용하여 한국어 문서를 AI가 읽기 쉬운 마크다운(Markdown) 텍스트로 고속 변환합니다.
- 🎓 **초등 교육과정 & 업무 맞춤형 분류**:
  - **수업자료**: 2022 개정 교육과정을 반영하여 **`학년` ➔ `과목` ➔ `단원` ➔ `차시`** 4단계 세부 폴더 자동 생성
  - **학급운영/생활지도**: 학급기초, 상담, 체험학습, 알림장 분류
  - **담당업무/행정/예산**: AI가 문서 내용을 분석하여 업무 영역(예: `정보교육`, `교육복지`, `회계_품의` 등)을 자율 판단 후 폴더링
- ⚡ **CPU 멀티프로세싱 병렬 파싱**: `concurrent.futures`를 통해 대용량 문서 파싱 속도를 5~7배 이상 단축했습니다.
- 📊 **실시간 프로그레스 바**: `tqdm`을 활용해 파싱, API 분석, 파일 이동의 3단계 진행 상황을 시각적으로 제공합니다.
- 💰 **극강의 토큰/비용 최적화**:
  - 배치 처리(`BATCH_SIZE=20`) 및 본문 슬라이싱 기법 적용
  - 초경량 `gemini-3.1-flash-lite` 모델 사용으로 **문서 10,000개 분류 시 약 2,000원 안팎**의 저렴한 비용
- 🛡️ **안전성 및 연속성**:
  - **체크포인트 재개 기능**: 중단되더라도 `progress.json`을 통해 멈춘 지점부터 다시 실행
  - **OS 예외 처리**: 윈도우 경로 금지 특수문자 정제(`sanitize_rel_path`) 및 파일명 충돌 자동 해결

---

## 📁 자동 분류 폴더 구조 예시

```text
documents/ (정리 대상 폴더)
│
├── 📁 01_학급운영_학생지도
│   ├── 01-1_학급기초 (명부, 자리배치, 비상연락망 등)
│   ├── 01-2_상담_생활지도
│   ├── 01-3_체험학습_출결
│   └── 01-4_가정통신문_알림장
│
├── 📁 02-1_수업자료_학습지 (2022 개정 교육과정 적용)
│   └── 📁 5학년
│       └── 📁 영어
│           └── 📁 08단원_How_much_are_the_shoes
│               ├── 📁 01차시
│               └── 📁 02차시
│
├── 📁 02-2_교육과정_진도 (1~6학년 하위 폴더)
├── 📁 02-3_평가_성적 (1~6학년 하위 폴더)
│
├── 📁 03_담당업무_예산_행정 (AI 자율 분류)
│   ├── 📁 정보교육
│   ├── 📁 교육복지
│   └── 📁 회계_품의
│
├── 📁 04_복무_연수_인사 (출장, 연수, 성과급 등)
└── 📁 99_기타문서
🛠️ 요구 사항 (Prerequisites)
Python: 3.10 이상

Node.js: 문서 변환 엔진인 kordoc 실행을 위해 필요 (Node.js 공식 다운로드)

kordoc: HWP, HWPX, PDF 마크다운 변환 엔진 (npx -y kordoc을 통해 실행 시 자동 로드)

Google AI Studio API Key: Gemini API Key 발급받기

🚀 설치 및 설정 (Installation)
1. Repository 클론
Bash
git clone [https://github.com/Dongnyeok-code/MySorter.git](https://github.com/Dongnyeok-code/MySorter.git)
cd MySorter
2. 필요 파이썬 패키지 설치
Bash
pip install google-genai tqdm python-dotenv
3. .env 환경 변수 설정
프로젝트 최상위 루트 경로에 .env 파일을 생성하고 발급받은 Gemini API 키를 입력합니다.

코드 스니펫
GEMINI_API_KEY=your_gemini_api_key_here
💻 사용 방법 (Usage)
문서 준비: 프로젝트 폴더 내에 documents 폴더를 생성하고, 분류하고 싶은 HWP, HWPX, PDF 파일들을 넣습니다.

Bash
mkdir documents
프로그램 실행:

Bash
python sorter.py
진행 상황 확인:
터미널 화면에 출력되는 3단계 프로그레스 바를 통해 파싱, 분석, 이동 과정을 실시간으로 확인할 수 있습니다.

🔒 보안 (Security & Privacy)
.gitignore 설정을 통해 개인 문서가 들어있는 documents/ 폴더, 중간 상태 기록 파일인 progress.json, API 키가 포함된 .env 파일은 Git 추적에서 완전히 제외되어 깃허브에 업로드되지 않습니다.

🙏 출처 및 감사 (Credits & Acknowledgments)
본 프로그램은 HWP, HWPX, PDF 등 한국어 문서를 AI가 쉽게 해석할 수 있는 마크다운 텍스트로 깔끔하게 파싱해 주는 kordoc 모듈을 핵심 파서 엔진으로 사용하고 있습니다. 훌륭한 도구를 공개해 주신 딴짓하는 류주임님께 감사드립니다.
* https://github.com/chrisryugj/kordoc.git
