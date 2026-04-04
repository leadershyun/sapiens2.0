"""
Sapiens2.0 - AI Agent Prototype
================================
OpenClaw과 유사한 구조의 AI 에이전트 프로토타입입니다.

기능:
  1. CMD(터미널)에서 에이전트와 대화
  2. 파일 시스템 탐색·수정·실행 등 컴퓨터 통제
  3. 사용자의 GitHub Copilot 계정 연동 (코드 생성)

사용법:
  python main.py                        # 실행 (토큰 미입력 시 실행 중 설정 가능)
  python main.py --token <GITHUB_TOKEN> # GitHub OAuth/PAT 토큰 직접 지정

주요 명령어 (실행 후 입력):
  /auth                 GitHub Copilot 인증 (device flow)
  /auth <token>         GitHub OAuth/PAT 토큰 직접 입력
  /pwd                  현재 작업 디렉토리 출력
  /ls [경로]            디렉토리 목록 출력
  /cat <파일>           파일 내용 출력
  /write <파일> <내용>  파일 작성 (확인 필요)
  /rm <파일>            파일 삭제 (확인 필요)
  /run <파일>           Python 파일 실행
  /exec <명령>          셸 명령 실행
  /codegen <설명>       Copilot으로 코드 생성
  /help                 도움말 출력
  /exit 또는 /quit      프로그램 종료

예시 흐름:
  [사용자] 현재 작업폴더 보여줘
  [에이전트] /pwd 명령을 사용하거나, 직접 /pwd 를 입력하세요.
  [사용자] /pwd
  [Sapiens2.0] 현재 폴더: /home/user/sapiens2.0
  [사용자] /codegen 헬로 sapiens2.0을 출력하는 파이썬 코드
  [Sapiens2.0] Copilot에게 코드 요청 중...
  [Sapiens2.0] 생성된 코드: print('hello sapiens2.0')

의존성 (requirements.txt 참조):
  pip install requests
"""

import argparse
import os
import subprocess
import sys
import time
import json
import textwrap
from typing import List, Optional, Union

try:
    import requests
except ImportError:
    print("[오류] 'requests' 패키지가 필요합니다. 다음 명령을 실행하세요: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────
#  상수 / 설정
# ─────────────────────────────────────────────

# GitHub OAuth App Client ID.
# device flow 인증을 위해 자신의 GitHub OAuth App을 등록하고
# 해당 App의 Client ID를 --client-id 인자로 전달하거나 아래 값을 교체하세요.
# GitHub OAuth App 등록: https://github.com/settings/developers
# 아래 기본값은 빈 문자열이며, device flow 사용 시 반드시 --client-id 를 지정해야 합니다.
DEFAULT_CLIENT_ID = ""  # 예: "Ov23liABCDEF1234abcd" (본인의 OAuth App Client ID)

# GitHub Copilot 내부 API 엔드포인트
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"

# GitHub device flow 엔드포인트
GH_DEVICE_CODE_URL = "https://github.com/login/device/code"
GH_TOKEN_URL = "https://github.com/login/oauth/access_token"

# Copilot API 기본 모델 (환경에 따라 변경 가능)
COPILOT_DEFAULT_MODEL = "gpt-4o"

# Copilot API 토큰 유효 시간 (초). Copilot 토큰은 약 30분간 유효합니다.
COPILOT_TOKEN_LIFETIME_SECONDS = 1800

# 위험 작업 목록 (실행 전 사용자 확인 요청)
DANGEROUS_EXTENSIONS = {".sh", ".bat", ".cmd", ".ps1", ".exe"}
CONFIRM_REQUIRED_COMMANDS = {"rm", "del", "rmdir", "rd", "format", "mkfs", "dd"}


# ─────────────────────────────────────────────
#  모듈 1: GitHub Copilot 연동
# ─────────────────────────────────────────────

class CopilotModule:
    """
    GitHub Copilot 연동 모듈.

    인증 방식:
      1. 직접 토큰 입력: set_token(github_token)
      2. GitHub device flow: authenticate_device_flow(client_id)

    토큰 종류:
      - GitHub PAT (Personal Access Token) 또는 OAuth token
      - 내부적으로 Copilot API 전용 토큰으로 교환하여 사용
    """

    def __init__(self):
        self._github_token: Optional[str] = None   # GitHub OAuth/PAT 토큰
        self._copilot_token: Optional[str] = None  # Copilot API 토큰 (만료 30분)
        self._copilot_token_expires: float = 0  # 만료 시각 (epoch seconds)

    # ── 토큰 설정 ──────────────────────────────

    def set_token(self, github_token: str) -> None:
        """GitHub OAuth/PAT 토큰을 직접 설정합니다."""
        self._github_token = github_token.strip()
        self._copilot_token = None  # 기존 Copilot 토큰 초기화
        print("[Copilot] GitHub 토큰이 설정되었습니다.")

    def is_authenticated(self) -> bool:
        """GitHub 토큰이 설정되어 있는지 확인합니다."""
        return bool(self._github_token)

    # ── Device Flow 인증 ───────────────────────

    def authenticate_device_flow(self, client_id: str = DEFAULT_CLIENT_ID) -> bool:
        """
        GitHub device flow를 사용해 사용자 인증을 수행합니다.

        1. GitHub에서 device code와 user code를 받습니다.
        2. 사용자가 브라우저에서 코드를 입력/승인합니다.
        3. 승인 완료 시 GitHub OAuth 토큰을 저장합니다.

        Returns:
            True: 인증 성공, False: 인증 실패
        """
        print("[Copilot] GitHub device flow 인증을 시작합니다...")

        # 1단계: device code 요청
        try:
            resp = requests.post(
                GH_DEVICE_CODE_URL,
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "scope": "copilot"},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[Copilot 오류] device code 요청 실패: {e}")
            return False

        data = resp.json()
        device_code = data.get("device_code")
        user_code = data.get("user_code")
        verification_uri = data.get("verification_uri", "https://github.com/login/device")
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 900)

        print(f"\n  다음 URL을 브라우저에서 열고 아래 코드를 입력하세요:")
        print(f"  URL  : {verification_uri}")
        print(f"  코드 : {user_code}")
        print(f"  (유효 시간: {expires_in}초)\n")

        # 2단계: 승인 폴링
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                poll_resp = requests.post(
                    GH_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    timeout=10,
                )
                poll_resp.raise_for_status()
            except requests.RequestException as e:
                print(f"[Copilot 오류] 토큰 폴링 실패: {e}")
                return False

            poll_data = poll_resp.json()
            if "access_token" in poll_data:
                self._github_token = poll_data["access_token"]
                self._copilot_token = None
                print("[Copilot] ✅ GitHub 인증 성공!")
                return True

            error = poll_data.get("error", "")
            if error == "authorization_pending":
                print("[Copilot] 승인 대기 중... (브라우저에서 코드를 입력해주세요)")
            elif error == "slow_down":
                interval += 5
            elif error in ("expired_token", "access_denied"):
                print(f"[Copilot 오류] {error}")
                return False

        print("[Copilot 오류] 인증 시간이 초과되었습니다.")
        return False

    # ── Copilot API 토큰 교환 ──────────────────

    def _get_copilot_token(self) -> Optional[str]:
        """
        GitHub OAuth 토큰을 Copilot API 전용 토큰으로 교환합니다.
        토큰은 30분 유효하며, 만료 시 자동으로 갱신됩니다.

        Returns:
            Copilot API 토큰 문자열 또는 None (실패 시)
        """
        if not self._github_token:
            print("[Copilot 오류] GitHub 토큰이 설정되지 않았습니다. /auth 명령을 사용하세요.")
            return None

        # 토큰이 유효하면 재사용
        if self._copilot_token and time.time() < self._copilot_token_expires - 60:
            return self._copilot_token

        try:
            resp = requests.get(
                COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {self._github_token}",
                    "Accept": "application/json",
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status == 401:
                print("[Copilot 오류] GitHub 토큰이 유효하지 않습니다. 다시 /auth 를 실행하세요.")
            elif status == 403:
                print("[Copilot 오류] GitHub Copilot 접근 권한이 없습니다. Copilot 구독을 확인하세요.")
            else:
                print(f"[Copilot 오류] Copilot 토큰 교환 실패 (HTTP {status}): {e}")
            return None
        except requests.RequestException as e:
            print(f"[Copilot 오류] 네트워크 오류: {e}")
            return None

        token_data = resp.json()
        self._copilot_token = token_data.get("token")
        expires_at = token_data.get("expires_at", 0)
        self._copilot_token_expires = (
            float(expires_at) if expires_at else time.time() + COPILOT_TOKEN_LIFETIME_SECONDS
        )
        return self._copilot_token

    # ── 코드 생성 / 채팅 ───────────────────────

    def generate_code(self, prompt: str, language: str = "python") -> Optional[str]:
        """
        Copilot을 사용해 코드를 생성합니다.

        Args:
            prompt: 생성할 코드 설명 (자연어)
            language: 코드 언어 (기본값: python)

        Returns:
            생성된 코드 문자열 또는 None (실패 시)
        """
        system_msg = (
            f"You are GitHub Copilot, an expert {language} programmer. "
            "Respond ONLY with clean, runnable code. "
            "No explanation, no markdown fences, just the code itself."
        )
        return self._call_copilot_api(system_msg, prompt)

    def chat(self, message: str) -> Optional[str]:
        """
        Copilot과 자연어 대화를 수행합니다.

        Args:
            message: 사용자 메시지

        Returns:
            Copilot의 응답 문자열 또는 None (실패 시)
        """
        system_msg = (
            "You are Sapiens2.0, a helpful AI agent assistant. "
            "Answer concisely in the same language the user writes in."
        )
        return self._call_copilot_api(system_msg, message)

    def _call_copilot_api(self, system_prompt: str, user_message: str) -> Optional[str]:
        """Copilot Chat Completions API를 호출합니다."""
        copilot_token = self._get_copilot_token()
        if not copilot_token:
            return None

        payload = {
            "model": COPILOT_DEFAULT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
        }

        try:
            resp = requests.post(
                COPILOT_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {copilot_token}",
                    "Content-Type": "application/json",
                    "Copilot-Integration-Id": "sapiens2.0",
                    "Editor-Version": "Sapiens2.0/1.0",
                    "Editor-Plugin-Version": "sapiens2.0/1.0",
                },
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            print(f"[Copilot 오류] API 호출 실패 (HTTP {status}): {e}")
            return None
        except requests.RequestException as e:
            print(f"[Copilot 오류] 네트워크 오류: {e}")
            return None

        try:
            result = resp.json()
            return result["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as e:
            print(f"[Copilot 오류] 응답 파싱 실패: {e}")
            return None


# ─────────────────────────────────────────────
#  모듈 2: 시스템 명령 (컴퓨터 통제)
# ─────────────────────────────────────────────

class SystemCommandModule:
    """
    파일 시스템 탐색, 파일 수정, 명령 실행 등 컴퓨터 통제 모듈.

    안전 정책:
      - 파일 삭제, 민감 명령 실행 시 사용자 확인 요구
      - 위험 확장자(.sh, .bat, .exe 등) 실행 시 경고 표시
    """

    def __init__(self):
        self._cwd = os.getcwd()  # 현재 작업 디렉토리

    def get_cwd(self) -> str:
        """현재 작업 디렉토리를 반환합니다."""
        return self._cwd

    def change_dir(self, path: str) -> str:
        """
        작업 디렉토리를 변경합니다.

        Args:
            path: 변경할 경로 (절대 또는 상대 경로)

        Returns:
            결과 메시지 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isdir(target):
            return f"[오류] 디렉토리가 존재하지 않습니다: {target}"
        self._cwd = target
        os.chdir(target)
        return f"현재 폴더: {self._cwd}"

    def list_dir(self, path: str = ".") -> str:
        """
        디렉토리 목록을 반환합니다.

        Args:
            path: 목록을 볼 경로 (기본값: 현재 디렉토리)

        Returns:
            파일/폴더 목록 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.exists(target):
            return f"[오류] 경로가 존재하지 않습니다: {target}"

        try:
            entries = os.listdir(target)
        except PermissionError:
            return f"[오류] 접근 권한이 없습니다: {target}"

        if not entries:
            return f"{target} (비어 있음)"

        lines = [f"📁 {target}"]
        for entry in sorted(entries):
            full = os.path.join(target, entry)
            prefix = "📂" if os.path.isdir(full) else "📄"
            lines.append(f"  {prefix} {entry}")
        return "\n".join(lines)

    def read_file(self, path: str) -> str:
        """
        파일 내용을 읽어 반환합니다.

        Args:
            path: 파일 경로

        Returns:
            파일 내용 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isfile(target):
            return f"[오류] 파일이 존재하지 않습니다: {target}"

        try:
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
            return f"--- {target} ---\n{content}\n---"
        except UnicodeDecodeError:
            return f"[오류] 바이너리 파일은 읽을 수 없습니다: {target}"
        except PermissionError:
            return f"[오류] 접근 권한이 없습니다: {target}"

    def write_file(self, path: str, content: str) -> str:
        """
        파일에 내용을 작성합니다. 기존 파일이 있으면 사용자 확인을 요청합니다.

        Args:
            path: 파일 경로
            content: 작성할 내용

        Returns:
            결과 메시지 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))

        if os.path.exists(target):
            if not _confirm(f"⚠️  '{target}' 파일이 이미 존재합니다. 덮어쓰겠습니까?"):
                return "취소되었습니다."

        try:
            dirname = os.path.dirname(target)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return f"✅ 파일 작성 완료: {target}"
        except PermissionError:
            return f"[오류] 접근 권한이 없습니다: {target}"

    def delete_file(self, path: str) -> str:
        """
        파일을 삭제합니다. 항상 사용자 확인을 요청합니다.

        Args:
            path: 삭제할 파일 경로

        Returns:
            결과 메시지 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.exists(target):
            return f"[오류] 파일이 존재하지 않습니다: {target}"

        if not _confirm(f"⚠️  '{target}' 파일을 삭제하겠습니까? (되돌릴 수 없습니다)"):
            return "취소되었습니다."

        try:
            os.remove(target)
            return f"✅ 파일 삭제 완료: {target}"
        except PermissionError:
            return f"[오류] 접근 권한이 없습니다: {target}"
        except IsADirectoryError:
            return f"[오류] 디렉토리는 /rm 으로 삭제할 수 없습니다. rmdir를 사용하세요."

    def run_file(self, path: str) -> str:
        """
        Python 스크립트 또는 셸 스크립트를 실행합니다.
        위험 확장자 파일 실행 시 사용자 확인을 요청합니다.

        Args:
            path: 실행할 파일 경로

        Returns:
            실행 결과 문자열
        """
        target = os.path.abspath(os.path.join(self._cwd, path))
        if not os.path.isfile(target):
            return f"[오류] 파일이 존재하지 않습니다: {target}"

        ext = os.path.splitext(target)[1].lower()

        if ext in DANGEROUS_EXTENSIONS:
            if not _confirm(f"⚠️  '{target}'은 위험한 파일 유형입니다. 실행하겠습니까?"):
                return "취소되었습니다."

        if ext == ".py":
            cmd = [sys.executable, target]
        else:
            cmd = [target]

        return _run_subprocess(cmd, cwd=self._cwd)

    def exec_command(self, command: str) -> str:
        """
        셸 명령을 실행합니다. 위험 명령어 실행 시 사용자 확인을 요청합니다.

        Args:
            command: 실행할 셸 명령어

        Returns:
            실행 결과 문자열
        """
        first_word = command.strip().split()[0].lower() if command.strip() else ""
        if first_word in CONFIRM_REQUIRED_COMMANDS:
            if not _confirm(f"⚠️  '{command}' 명령은 위험할 수 있습니다. 실행하겠습니까?"):
                return "취소되었습니다."

        return _run_subprocess(command, shell=True, cwd=self._cwd)


# ─────────────────────────────────────────────
#  모듈 3: 에이전트 Core
# ─────────────────────────────────────────────

class AgentCore:
    """
    Sapiens2.0 에이전트 핵심 모듈.

    사용자 입력을 분석하여 적절한 모듈(Copilot, System)로 라우팅합니다.
    """

    def __init__(self, github_token: Optional[str] = None, client_id: str = DEFAULT_CLIENT_ID):
        self.copilot = CopilotModule()
        self.system = SystemCommandModule()
        self.client_id = client_id

        if github_token:
            self.copilot.set_token(github_token)

    def process(self, user_input: str) -> str:
        """
        사용자 입력을 처리하고 결과를 반환합니다.

        슬래시 명령(/cmd)은 직접 처리하고,
        일반 텍스트는 Copilot 채팅으로 전달합니다.

        Args:
            user_input: 사용자 입력 문자열

        Returns:
            에이전트 응답 문자열
        """
        raw = user_input.strip()
        if not raw:
            return ""

        # ── 슬래시 명령 처리 ────────────────────
        if raw.startswith("/"):
            return self._handle_slash_command(raw)

        # ── 자연어 입력 → Copilot 채팅 ──────────
        if not self.copilot.is_authenticated():
            return (
                "💬 Copilot이 연결되어 있지 않습니다.\n"
                "  /auth <token>  을 사용해 GitHub 토큰을 설정하거나,\n"
                "  /auth          를 입력해 device flow 인증을 시작하세요.\n\n"
                "  슬래시 명령(/pwd, /ls, /help 등)은 인증 없이 사용 가능합니다."
            )

        response = self.copilot.chat(raw)
        return response if response else "[오류] Copilot 응답을 받지 못했습니다."

    def _handle_slash_command(self, raw: str) -> str:
        """슬래시(/) 명령을 파싱하고 실행합니다."""
        parts = raw.split(None, 2)  # 최대 3개 토큰 분리
        cmd = parts[0].lower()
        arg1 = parts[1] if len(parts) > 1 else ""
        arg2 = parts[2] if len(parts) > 2 else ""

        # ── 인증 ────────────────────────────────
        if cmd == "/auth":
            if arg1:
                # 직접 토큰 입력
                self.copilot.set_token(arg1)
                return "✅ GitHub 토큰이 설정되었습니다."
            else:
                # Device flow 인증
                ok = self.copilot.authenticate_device_flow(self.client_id)
                return "✅ 인증 완료!" if ok else "❌ 인증 실패. 다시 시도하세요."

        # ── 파일 시스템 ─────────────────────────
        if cmd == "/pwd":
            return f"현재 폴더: {self.system.get_cwd()}"

        if cmd == "/cd":
            if not arg1:
                return "[오류] 사용법: /cd <경로>"
            return self.system.change_dir(arg1)

        if cmd == "/ls":
            return self.system.list_dir(arg1 if arg1 else ".")

        if cmd == "/cat":
            if not arg1:
                return "[오류] 사용법: /cat <파일>"
            return self.system.read_file(arg1)

        if cmd == "/write":
            if not arg1:
                return "[오류] 사용법: /write <파일> <내용>"
            content = arg2
            if not content:
                print(f"  '{arg1}'에 작성할 내용을 입력하세요 (빈 줄에서 Ctrl+D 또는 'EOF'로 종료):")
                lines = []
                try:
                    while True:
                        line = input()
                        if line == "EOF":
                            break
                        lines.append(line)
                except EOFError:
                    pass
                content = "\n".join(lines)
            return self.system.write_file(arg1, content)

        if cmd == "/rm":
            if not arg1:
                return "[오류] 사용법: /rm <파일>"
            return self.system.delete_file(arg1)

        # ── 실행 ────────────────────────────────
        if cmd == "/run":
            if not arg1:
                return "[오류] 사용법: /run <파일>"
            return self.system.run_file(arg1)

        if cmd == "/exec":
            if not arg1:
                return "[오류] 사용법: /exec <명령>"
            full_cmd = raw[len("/exec "):].strip()
            return self.system.exec_command(full_cmd)

        # ── Copilot 코드 생성 ────────────────────
        if cmd == "/codegen":
            if not self.copilot.is_authenticated():
                return "❌ Copilot 인증이 필요합니다. /auth 를 먼저 실행하세요."

            # 전체 입력에서 /codegen 이후 부분을 가져와 --lang 플래그 파싱
            codegen_args = raw[len("/codegen"):].strip()
            lang = "python"
            if "--lang" in codegen_args:
                lang_parts = codegen_args.split("--lang", 1)
                codegen_args = lang_parts[0].strip()
                lang_value = lang_parts[1].strip().split()[0] if lang_parts[1].strip() else ""
                if lang_value:
                    lang = lang_value

            if not codegen_args:
                return "[오류] 사용법: /codegen <코드 설명> [--lang <언어>]"

            print(f"  Copilot에게 {lang} 코드를 요청 중...")
            code = self.copilot.generate_code(codegen_args, language=lang)
            if code:
                return f"생성된 코드:\n\n{code}"
            return "❌ 코드 생성 실패."

        # ── 도움말 ───────────────────────────────
        if cmd in ("/help", "/?"):
            return _help_text()

        # ── 종료 ────────────────────────────────
        if cmd in ("/exit", "/quit", "/q"):
            print("Sapiens2.0을 종료합니다. 안녕히 가세요! 👋")
            sys.exit(0)

        return f"[오류] 알 수 없는 명령: {cmd}\n/help 를 입력하면 명령어 목록을 볼 수 있습니다."


# ─────────────────────────────────────────────
#  유틸리티 함수
# ─────────────────────────────────────────────

def _confirm(message: str) -> bool:
    """
    사용자에게 예/아니오 확인을 요청합니다.

    Args:
        message: 확인 메시지

    Returns:
        True: 사용자가 확인(y/yes), False: 취소(n/no 또는 기타)
    """
    try:
        answer = input(f"{message} [y/N] ").strip().lower()
        return answer in ("y", "yes", "예", "ㅇ")
    except (EOFError, KeyboardInterrupt):
        return False


def _run_subprocess(cmd: Union[str, List[str]], cwd: str = ".", shell: bool = False, timeout: int = 30) -> str:
    """
    서브프로세스를 실행하고 결과를 반환합니다.

    Args:
        cmd: 실행할 명령 (문자열 또는 리스트)
        cwd: 작업 디렉토리
        shell: True면 셸을 통해 실행
        timeout: 타임아웃 (초)

    Returns:
        표준 출력 + 표준 에러 결합 문자열
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            shell=shell,
            timeout=timeout,
        )
        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"[stderr]\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"[종료 코드: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "(출력 없음)"

    except subprocess.TimeoutExpired:
        return f"[오류] 명령 실행 시간 초과 ({timeout}초)"
    except FileNotFoundError as e:
        return f"[오류] 명령 또는 파일을 찾을 수 없습니다: {e}"
    except PermissionError as e:
        return f"[오류] 실행 권한이 없습니다: {e}"


def _help_text() -> str:
    """도움말 텍스트를 반환합니다."""
    return textwrap.dedent("""\
        ╔══════════════════════════════════════════╗
        ║          Sapiens2.0 명령어 도움말          ║
        ╚══════════════════════════════════════════╝

        [인증]
          /auth                GitHub device flow 인증 시작
          /auth <token>        GitHub OAuth/PAT 토큰 직접 설정

        [파일 시스템]
          /pwd                 현재 작업 디렉토리 출력
          /cd <경로>           작업 디렉토리 변경
          /ls [경로]           디렉토리 목록 출력
          /cat <파일>          파일 내용 출력
          /write <파일> [내용] 파일 작성 (내용 생략 시 입력 모드)
          /rm <파일>           파일 삭제 (확인 필요)

        [실행]
          /run <파일>          Python/스크립트 파일 실행
          /exec <명령>         셸 명령 실행

        [Copilot 연동]
          /codegen <설명> [--lang <언어>]   설명을 기반으로 코드 생성 (기본: python)

        [기타]
          /help  또는  /?      이 도움말 출력
          /exit  또는  /quit   프로그램 종료

        슬래시 명령 외 일반 텍스트 입력은 Copilot과의 대화로 처리됩니다.
        (Copilot 인증 필요)
    """)


# ─────────────────────────────────────────────
#  진입점
# ─────────────────────────────────────────────

def main():
    """Sapiens2.0 에이전트를 시작합니다."""
    parser = argparse.ArgumentParser(
        description="Sapiens2.0 - AI Agent (GitHub Copilot 연동)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            예시:
              python main.py                        # 기본 실행
              python main.py --token ghp_xxx...     # 토큰 직접 지정
              python main.py --client-id abc123     # 커스텀 OAuth App ID 사용
        """),
    )
    parser.add_argument(
        "--token",
        metavar="GITHUB_TOKEN",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub OAuth/PAT 토큰 (환경변수 GITHUB_TOKEN도 인식)",
    )
    parser.add_argument(
        "--client-id",
        metavar="CLIENT_ID",
        default=DEFAULT_CLIENT_ID,
        help=(
            "GitHub OAuth App Client ID. device flow 인증(/auth) 사용 시 필수. "
            "GitHub OAuth App 등록: https://github.com/settings/developers"
        ),
    )
    args = parser.parse_args()

    # 에이전트 초기화
    agent = AgentCore(github_token=args.token, client_id=args.client_id)

    print("=" * 50)
    print("  Sapiens2.0 AI Agent - 프로토타입 v1.0")
    print("=" * 50)
    print("  /help 를 입력하면 명령어 목록을 볼 수 있습니다.")
    if args.token:
        print("  ✅ GitHub 토큰이 설정되었습니다.")
    else:
        print("  ℹ️  Copilot 연동을 위해 /auth 를 실행하거나")
        print("     --token <GITHUB_TOKEN> 옵션을 사용하세요.")
    print("=" * 50)
    print()

    # 대화 루프
    while True:
        try:
            user_input = input("[사용자] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSapiens2.0을 종료합니다. 안녕히 가세요! 👋")
            break

        if not user_input:
            continue

        response = agent.process(user_input)
        if response:
            print(f"[Sapiens2.0] {response}\n")


if __name__ == "__main__":
    main()
