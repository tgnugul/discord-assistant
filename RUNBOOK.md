# 운영 가이드 (RUNBOOK)

봇을 운영하다 문제가 생겼을 때 대처법. 봇은 **Oracle Cloud VM**에서 `systemd` 서비스(`discord-bot`)로 24/7 실행된다.

> 이 문서는 공개 리포에 있으므로 실제 IP·토큰·키는 적지 않는다.
> `<VM_PUBLIC_IP>`, `<키파일경로>` 는 본인 값으로 바꿔 사용.

---

## 0. 접속 & 기본 명령 (제일 먼저 외울 것)

```bash
# VM 접속 (로컬 PowerShell/터미널에서)
ssh -i "<키파일경로>" ubuntu@<VM_PUBLIC_IP>

# --- 이하 VM 안에서 ---
systemctl status discord-bot        # 살아있는지 확인 (active면 정상)
sudo systemctl restart discord-bot  # 재시작
sudo systemctl stop discord-bot     # 정지
sudo systemctl start discord-bot    # 시작
journalctl -u discord-bot -n 50 --no-pager   # 최근 로그 50줄
journalctl -u discord-bot -f                 # 실시간 로그 (Ctrl+C로 종료)
```

---

## 1. 봇이 Discord에서 오프라인(회색)일 때

1. 접속해서 상태 확인: `systemctl status discord-bot`
   - **active (running)** 인데 오프라인이면 → 네트워크/디스코드 일시 문제. 잠시 후 자동 복구되거나 `sudo systemctl restart discord-bot`.
   - **failed / inactive** 이면 → 아래 로그 확인.
2. 로그 확인: `journalctl -u discord-bot -n 50 --no-pager`
   - `LoginFailure` / `Improper token` → **디스코드 봇 토큰 문제**. `.env`의 `DISCORD_TOKEN` 확인(디스코드 개발자 포털에서 재발급 후 교체).
   - `ModuleNotFoundError` → 의존성 문제. `cd ~/discord-assistant && .venv/bin/pip install -r requirements.txt` 후 재시작.

## 2. 서버(VM)가 꺼졌거나 재부팅됐을 때

- `systemd`가 **자동 재시작**하도록 등록돼 있어, VM이 켜지면 봇도 자동 실행된다. 보통 아무것도 안 해도 됨.
- VM 자체가 꺼져 있으면 → Oracle 콘솔 → Compute → Instances → 인스턴스 → **Start**.
- ⚠️ **Public IP가 임시(ephemeral)** 라, VM을 Stop 후 Start하면 **IP가 바뀔 수 있다.** 접속이 안 되면 콘솔에서 새 Public IP를 확인해 `ssh` 주소를 갱신할 것.
  - IP가 바뀌는 게 싫으면 **Reserved public IP**로 전환(콘솔 → Networking → 해당 VNIC → IP 관리).

## 3. 데브 로그가 GitHub(daily-report)에 안 올라올 때

먼저 원인 구분 — 자동 커밋은 **매일 06:00(KST)**, 전날 메모 대상.

- **그날(전날) 메모가 아예 없었다** → 정상. 내용 없으면 커밋 안 함.
- **Discord에 `❌ ... 커밋 실패` 알림이 왔다** → 알림에 찍힌 에러 메시지로 판단:
  - `git push ... 실패` + `403`/인증 관련 → **GitHub 토큰(PAT) 만료/권한** 문제. 새 PAT 발급(`daily-report` Contents: Read/write) 후 `.env`의 `GITHUB_TOKEN` 교체 → 재시작.
  - `git pull ... 실패` → 리포가 꼬였을 수 있음. VM에서 `cd ~/daily-report && git status` 확인, 필요 시 `git pull --rebase origin main`.
- **Discord에 `⚠️ ... 민감 정보 의심` 알림이 왔다** → 그 카테고리 메모에 토큰/비번 같은 패턴이 있어 **안전하게 커밋을 막은 것**. 해당 메모를 확인해 민감 정보를 빼거나, 안전하면 수동으로 정리해 올릴 것. (오작동 아님)
- **아무 알림도 없고 그냥 안 올라왔다** → 봇이 06시에 안 떠 있었을 가능성. `systemctl status`로 확인. 수동으로 만들려면 Discord에서 `/업로드 날짜:YYYY-MM-DD`.

## 4. 수동으로 데브 로그 만들기

Discord에서:
```
/업로드              → 어제치 생성·커밋
/업로드 날짜:2026-07-06  → 특정 날짜치 생성·커밋
```

## 5. 코드를 수정했을 때(업데이트)

```bash
cd ~/discord-assistant
git pull
.venv/bin/pip install -r requirements.txt   # 의존성 바뀌었을 때만
sudo systemctl restart discord-bot
```

## 6. 토큰/키 관리

- `.env`(VM의 `~/discord-assistant/.env`)에 모든 비밀값이 있다. **절대 커밋 금지**(`.gitignore`에 있음).
- GitHub PAT가 유출된 것 같으면 → GitHub Settings에서 revoke 후 재발급 → `.env`의 `GITHUB_TOKEN` 교체 → 재시작.
- `.env` 편집: `nano ~/discord-assistant/.env` → 저장(Ctrl+O, Enter) → 종료(Ctrl+X) → `sudo systemctl restart discord-bot`.

## 7. 자주 겪는 것 빠른 표

| 증상 | 먼저 볼 것 | 조치 |
|------|-----------|------|
| 봇 오프라인 | `systemctl status discord-bot` | 재시작 / 로그 확인 |
| SSH 접속 안 됨 | Public IP 바뀌었는지 | 콘솔에서 새 IP 확인 |
| 데브로그 안 올라옴 | Discord 알림 / 로그 | 3번 항목 참고 |
| `❌ 커밋 실패` 알림 | 알림의 에러 메시지 | 토큰/리포 상태 확인 |
| `⚠️ 민감정보` 알림 | 해당 메모 | 민감정보 제거(정상 동작) |
| 명령어(/요약 등) 안 뜸 | 봇 재시작 후 잠시 대기 | 슬래시 커맨드 재동기화 |
