# sapiens2.0

Sapiens2.0은 GitHub Copilot과 연동되는 AI 에이전트 프로토타입입니다.  
PowerShell(또는 터미널)에서 대화하고 파일 시스템을 제어할 수 있습니다.

## 요구사항

- Python 3.8 이상
- GitHub Copilot 구독 (인증 후 Copilot API 사용)
- [requests](https://pypi.org/project/requests/) 패키지

## 설치 (PowerShell)

```powershell
git clone https://github.com/leadershyun/sapiens2.0.git
cd sapiens2.0
pip install -r requirements.txt
```

## 실행

```powershell
python .\main.py
```

토큰을 미리 지정하려면:

```powershell
python .\main.py --token ghp_xxxxxxxxxxxx
```

또는 환경변수로:

```powershell
$env:GITHUB_TOKEN = "ghp_xxxxxxxxxxxx"
python .\main.py
```

## GitHub 인증 (OpenClaw 방식)

Sapiens2.0은 OpenClaw와 동일한 **GitHub device flow** 인증을 사용합니다.  
별도로 OAuth App을 등록하거나 PAT를 발급할 필요 없이, 실행 후 `/auth` 명령만 입력하면 됩니다.

### 인증 순서 (PowerShell)

```powershell
# 1. 프로그램 실행
python .\main.py

# 2. 인증 시작
[사용자] /auth

# 3. 터미널에 코드와 URL이 표시됩니다
[Sapiens2.0] GitHub device flow 인증을 시작합니다...
[Sapiens2.0]
  ┌─────────────────────────────────────────────────┐
  │  브라우저에서 아래 URL을 열고 코드를 입력하세요.  │
  │                                                 │
  │  URL  : https://github.com/login/device         │
  │  코드 : ABCD-1234                               │
  │                                                 │
  │  유효 시간: 899초                                │
  └─────────────────────────────────────────────────┘

# 4. 브라우저에서 https://github.com/login/device 로 이동
# 5. 터미널에 표시된 코드(예: ABCD-1234)를 입력하고 GitHub 계정으로 승인
# 6. 터미널에 ✅ 인증 성공 표시
[Sapiens2.0] ✅ GitHub 인증 성공!
[Sapiens2.0] ✅ 인증 완료!

# 7. 이제 Copilot 기능 사용 가능
[사용자] 안녕하세요!
[Sapiens2.0] 안녕하세요! 무엇을 도와드릴까요?
```

> **참고**: GitHub Copilot 구독이 활성화된 계정으로 승인해야 Copilot API를 사용할 수 있습니다.

## 주요 명령어

| 명령어 | 설명 |
|--------|------|
| `/auth` | GitHub device flow 인증 시작 (OpenClaw 방식, **권장**) |
| `/auth <token>` | GitHub PAT/OAuth 토큰 직접 입력 |
| `/status` | 현재 GitHub/Copilot 인증 상태 및 선택 모델 확인 |
| `/models` | 사용 가능한 Copilot 모델 목록 보기 |
| `/models <번호\|이름>` | 모델 선택 (예: `/models 2` 또는 `/models gpt-4o-mini`) |
| `/new` | 새 대화 시작 (단기 기억 초기화, 장기 기억 유지) |
| `/reset` | 전체 초기화 (장기 기억·모델 설정 포함, 확인 필요) |
| `/memory` | 현재 장기 기억 내용 보기 |
| `/pwd` | 현재 디렉토리 출력 |
| `/ls [경로]` | 디렉토리 목록 출력 |
| `/cat <파일>` | 파일 내용 출력 |
| `/write <파일>` | 파일 작성 |
| `/rm <파일>` | 파일 삭제 |
| `/run <파일>` | 파일 실행 |
| `/exec <명령>` | 셸 명령 실행 |
| `/codegen <설명>` | Copilot으로 코드 생성 |
| `/help` | 도움말 출력 |
| `/exit` | 종료 |

일반 텍스트 입력은 Copilot과의 대화로 처리됩니다 (인증 필요).

## 기억 시스템

Sapiens2.0은 OpenClaw에서 영감을 받은 **단기/장기 기억 분리** 구조를 사용합니다.

### 단기 기억 (Short-term Memory)
- 현재 세션의 대화 내역을 저장합니다.
- `/new` 명령 또는 세션 종료 시 자동으로 초기화됩니다.
- 모든 Copilot API 호출에 대화 맥락으로 포함됩니다 (최근 20개 메시지).

### 장기 기억 (Long-term Memory)
- `sapiens_memory.json` 파일에 JSON 형태로 저장됩니다.
- 세션 간에도 유지되며, `/reset` 명령으로만 삭제됩니다.
- **에이전트가 대화 중 자동으로 중요한 정보를 추출하여 장기 기억에 저장합니다.**
  - 사용자 선호도, 이전에 논의한 주제, 중요한 사실 등
- `/memory` 명령으로 현재 저장된 장기 기억을 확인할 수 있습니다.

```text
[사용자] /memory
[Sapiens2.0] [장기 기억]
  • preferred_language: Python
  • project_name: my-web-app
  • user_name: 홍길동
```

## 모델 선택

`/models` 명령으로 사용 가능한 Copilot 모델을 보고 선택할 수 있습니다.

```text
[사용자] /models
[Sapiens2.0] 사용 가능한 Copilot 모델:
  1. gpt-4o ◀ 현재 선택
  2. gpt-4o-mini
  3. gpt-4-turbo
  4. claude-3.5-sonnet
  5. o1-preview
  6. o1-mini

선택하려면: /models <번호 또는 모델명>

[사용자] /models 2
[Sapiens2.0] ✅ 모델이 'gpt-4o-mini'으로 변경되었습니다.
```

선택한 모델은 `sapiens_state.json`에 저장되어 다음 실행 시에도 유지됩니다.

## 컴퓨터 제어 기능

Sapiens2.0은 로컬 파일 시스템과 셸 명령을 제어할 수 있습니다.

| 기능 | 명령어 | 안전 정책 |
|------|--------|-----------|
| 디렉토리 이동 | `/cd <경로>` | 경로 존재 여부 확인 |
| 파일 목록 | `/ls [경로]` | 읽기 전용 |
| 파일 읽기 | `/cat <파일>` | 읽기 전용 |
| 파일 쓰기 | `/write <파일> [내용]` | 기존 파일 덮어쓰기 시 확인 |
| 파일 삭제 | `/rm <파일>` | 항상 확인 요청 |
| 스크립트 실행 | `/run <파일>` | 위험 확장자(.sh/.bat/.exe 등) 실행 시 확인 |
| 셸 명령 실행 | `/exec <명령>` | rm/del/format 등 위험 명령 시 확인 |

## 문제 해결 (Troubleshooting)

### GitHub 인증은 성공했지만 Copilot 응답이 실패하는 경우

인증 후 메시지를 보냈을 때 오류가 나면, `/status` 명령으로 상태를 먼저 확인하세요.

```text
[사용자] /status
```

오류 유형별 해결 방법:

| 오류 메시지 | 원인 | 해결 방법 |
|------------|------|-----------|
| `Copilot 토큰 교환이 거부되었습니다 (HTTP 403)` | Copilot 구독 없음 또는 OAuth 스코프 부족 | https://github.com/settings/copilot 구독 확인 후 `/auth` 재실행 |
| `GitHub 토큰이 유효하지 않거나 만료되었습니다 (HTTP 401)` | GitHub 토큰 만료 | `/auth` 재실행 |
| `Copilot Chat API 엔드포인트를 찾을 수 없습니다 (HTTP 404)` | API 주소 또는 모델명 오류 | 최신 버전 코드 확인 또는 `/models`로 다른 모델 선택 |
| `조직 SSO 정책으로 인해 토큰이 차단` | 조직 SSO 미승인 | GitHub SSO 승인 페이지에서 앱 승인 |

### 공통 확인 사항

1. GitHub 계정에 활성 Copilot 구독이 있는지 확인: https://github.com/settings/copilot
2. `/auth` 명령 실행 후 브라우저에서 코드를 정확히 입력했는지 확인
3. 인증 후 바로 `/status` 명령으로 토큰 상태 확인

## 저장 파일

| 파일 | 내용 | 삭제 방법 |
|------|------|-----------|
| `sapiens_memory.json` | 장기 기억 (대화에서 추출된 중요 정보) | `/reset` 명령 또는 직접 삭제 |
| `sapiens_state.json` | 에이전트 상태 (선택된 모델 등) | `/reset` 명령 또는 직접 삭제 |

두 파일 모두 `.gitignore`에 포함되어 Git에 커밋되지 않습니다.

## 고급: 직접 OAuth App 사용

자체 GitHub OAuth App을 등록해서 사용하려면:

```powershell
python .\main.py --client-id <OAuth_App_Client_ID>
```

OAuth App 등록: https://github.com/settings/developers
