# sapiens2.0

Sapiens2.0은 GitHub Copilot과 연동되는 AI 에이전트 프로토타입입니다.  
PowerShell(또는 터미널)에서 대화하고 파일 시스템을 제어할 수 있습니다.

## 요구사항

- Python 3.8 이상
- GitHub Copilot 구독 (PAT 토큰 발급용)
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

## GitHub PAT 발급 방법

1. https://github.com/settings/tokens 로 이동
2. **Generate new token (classic)** 클릭
3. 만료 기간을 설정하고 **Generate token** 클릭
4. 발급된 토큰(`ghp_...`)을 복사
5. GitHub Copilot 구독이 활성화된 계정이어야 합니다.

## 주요 명령어

| 명령어 | 설명 |
|--------|------|
| `/auth` | GitHub PAT 토큰 입력 프롬프트 |
| `/auth <token>` | PAT 토큰 직접 입력 |
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

## device flow 인증 (선택사항)

GitHub OAuth App을 직접 등록한 경우 device flow 인증도 사용할 수 있습니다:

```powershell
python .\main.py --client-id <OAuth_App_Client_ID>
# 실행 후 /auth 입력
```

OAuth App 등록: https://github.com/settings/developers
