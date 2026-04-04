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

## 고급: 직접 OAuth App 사용

자체 GitHub OAuth App을 등록해서 사용하려면:

```powershell
python .\main.py --client-id <OAuth_App_Client_ID>
```

OAuth App 등록: https://github.com/settings/developers
