# Oracle Cloud Always Free 배포 가이드

봇을 Oracle Cloud "Always Free" VM에서 24/7 무료로 돌리는 절차.
한 번 세팅하면 재부팅·크래시에도 `systemd`가 자동 재시작하며 영구히 지속된다.

> 전제: GitHub 리포 `tgnugul/discord-assistant`, `tgnugul/daily-report` 둘 다 public.
> clone은 인증 불필요, `daily-report` **push할 때만** GitHub 토큰(PAT) 필요.

---

## Part 1. Oracle Cloud VM 생성 (웹 콘솔에서 직접)

1. https://www.oracle.com/cloud/free/ 에서 무료 계정 가입 (신용카드 확인은 있으나 Always Free 자원은 과금 안 됨).
2. 콘솔 → **Compute → Instances → Create Instance**.
3. 설정:
   - **Image:** Canonical Ubuntu 22.04
   - **Shape:** `VM.Standard.E2.1.Micro` (AMD, Always Free) 권장.
     - ARM `VM.Standard.A1.Flex`(4 OCPU/24GB)가 더 좋지만 "out of capacity"가 잦음. 이 봇은 가벼워 E2.1.Micro(1GB)로 충분.
   - **SSH keys:** "Generate a key pair" 선택 후 **private key 다운로드** (로그인에 필요, 잘 보관).
4. Create. 잠시 후 인스턴스의 **Public IP** 확인.
5. **네트워크(egress)만 쓰면 되므로 인바운드 포트 개방 불필요.** (봇은 Discord에 바깥으로만 연결)

---

## Part 2. GitHub 토큰(PAT) 발급 — `daily-report` push용

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token.
2. 설정:
   - **Resource owner:** tgnugul
   - **Repository access:** Only select repositories → **`daily-report`** 만 선택
   - **Permissions → Repository → Contents:** **Read and write**
   - Expiration: 원하는 기간 (예: 1년)
3. 생성된 `github_pat_...` 문자열 복사 (한 번만 보임).

---

## Part 3. VM 접속 & 환경 세팅

```bash
# 로컬에서 SSH 접속 (다운로드한 키 사용)
chmod 600 your-key.pem
ssh -i your-key.pem ubuntu@<PUBLIC_IP>

# --- 이하 VM 안에서 ---

# 시간대를 한국으로 (cron 06:00 = KST, 날짜 계산 일관성)
sudo timedatectl set-timezone Asia/Seoul

# 패키지 설치
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip

# git 커밋 신원
git config --global user.name "tgnugul"
git config --global user.email "ghwn109@gmail.com"

# 봇 코드 clone
cd ~
git clone https://github.com/tgnugul/discord-assistant.git
cd discord-assistant

# 파이썬 가상환경 + 의존성
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 공개 리포(daily-report) clone — 데브 로그가 커밋될 로컬 클론
git clone https://github.com/tgnugul/daily-report.git ~/daily-report
```

### `.env` 작성 (VM 안, `~/discord-assistant/.env`)

```bash
nano ~/discord-assistant/.env
```

```
DISCORD_TOKEN=기존_디스코드_봇_토큰
OPENAI_API_KEY=기존_OpenAI_키
SUMMARY_CHANNEL_ID=기존_요약채널_ID
DEVLOG_REPO_PATH=/home/ubuntu/daily-report
GITHUB_TOKEN=github_pat_...    # Part 2에서 발급한 토큰
```

> `.env`, `memories.db`는 `.gitignore`에 있어 GitHub에 안 올라간다.
> `memories.db`는 VM에서 새로 생성되며, 봇 첫 실행 시 Discord 히스토리(채널당 500개)를 자동 임포트한다.

---

## Part 4. systemd 서비스 등록 (자동 시작 + 재시작)

```bash
sudo cp ~/discord-assistant/deploy/discord-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now discord-bot

# 상태 확인
systemctl status discord-bot
journalctl -u discord-bot -f      # 실시간 로그 (Ctrl+C로 종료)
```

정상이면 로그에 `봇 실행 중: ...`, `슬래시 커맨드 등록 완료`, 채널 임포트 로그가 뜬다.

---

## Part 5. 검증

- Discord에서 `/업로드` 실행 → 어제치 데브 로그가 생성·커밋되고 알림이 오는지 확인.
- https://github.com/tgnugul/daily-report 에 커밋이 올라오면 성공.
- 이후 매일 **06:00 KST** 자동 커밋.

## 업데이트 방법 (코드 수정 후)

```bash
cd ~/discord-assistant && git pull
sudo systemctl restart discord-bot
```
