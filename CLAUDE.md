# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 서비스 정의

**"Discord 서버를 나만의 두 번째 뇌로 — 채널에 기록하면 AI가 정리해준다"**

> 메모는 적는 순간이 아니라 다시 꺼낼 때 가치가 생긴다.

채널 이름을 카테고리 삼아 자유롭게 메모하면, AI가 요약·분류·정리를 자동으로 처리해주는 개인 비서 봇. ✅ 반응으로 완료 표시, `/요약`으로 즉시 조회, 매일 아침 리포트로 하루를 시작하는 것이 목표.

### 구현된 핵심 기능

| 기능 | 설명 |
|------|------|
| 자동 메모 저장 | 모든 채널 메시지를 SQLite에 저장 (channel명 = 카테고리) |
| 일일 요약 | 매일 오전 9시, 활성 메모를 GPT로 요약해 지정 채널에 전송 |
| 주간 자동 정리 | 매주 월요일 오전 9:10, AI가 완료됐을 가능성이 높은 항목을 제안 |
| 완료 처리 | 메시지에 ✅ 반응 → DB에서 `status='done'`으로 변경 |
| `/요약` 슬래시 커맨드 | 현재 채널의 최근 N일치 메모를 즉시 요약 (기본 90일) |
| `/질문` 슬래시 커맨드 | 저장된 전체 메모를 바탕으로 자유 질문에 답변 |
| 히스토리 임포트 | 봇 시작 시 서버의 기존 채널 메시지 500개를 자동 수집 |

### 구현 예정 (미완성)

| 항목 | 내용 |
|------|------|
| 일정 키워드 감지 | 날짜/시간 키워드 감지 시 자동 알림 |
| 오래된 메모 자동 정리 | 일정 기간이 지난 메모 아카이브 처리 |
| 채널별 통계 | 채널별 메모 수·완료율 리포트 |

---

## Project Overview

단일 파일(`bot.py`) 구조의 Discord AI 비서 봇.

```
discord-assistant/
├── bot.py          # 메인 봇 코드 (전체 로직)
├── .env            # API 키 및 설정 (절대 커밋 금지)
├── memories.db     # SQLite DB (자동 생성, 커밋 금지)
└── CLAUDE.md
```

## Commands

```bash
cd discord-assistant
pip install discord.py openai apscheduler python-dotenv
python bot.py
```

---

## Architecture (`bot.py`)

**인증 및 클라이언트**: `discord.Client` + `app_commands.CommandTree`. `DISCORD_TOKEN`으로 봇 인증. `Intents.message_content` + `Intents.reactions` 활성화 필수.

**AI**: OpenAI `gpt-4o-mini`. 일일 요약·주간 정리·슬래시 커맨드 응답에 사용.

**스케줄러**: `APScheduler AsyncIOScheduler`. `on_ready`에서 시작.
- 매일 09:00 → `send_daily_summary()`
- 매주 월요일 09:10 → `ai_weekly_cleanup()`

**완료 처리 흐름**: `on_raw_reaction_add` → ✅ 이모지 감지 → `mark_done(message_id)` → DB `status='done'`

### DB 스키마 (`memories.db`)

테이블: `messages`

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | 자동 증가 |
| message_id | TEXT UNIQUE | Discord 메시지 ID (중복 방지) |
| channel | TEXT | 채널 이름 (카테고리 역할) |
| content | TEXT | 메시지 내용 |
| timestamp | TEXT | ISO 8601 형식 |
| status | TEXT | `active` / `done` (기본값 `active`) |

**DB 마이그레이션**: 별도 도구 없이 `init_db()`에서 `ALTER TABLE ... ADD COLUMN` + `except OperationalError` 패턴으로 컬럼 추가. 새 컬럼 추가 시 같은 패턴을 사용할 것.

### 주요 함수

| 함수 | 역할 |
|------|------|
| `init_db()` | 테이블 생성 및 마이그레이션 |
| `save_message()` | 메시지 저장 (공백 무시, `INSERT OR IGNORE`) |
| `mark_done()` | ✅ 반응 시 완료 처리 |
| `get_all_messages()` | status 기준 전체 조회 |
| `get_channel_messages()` | 채널·기간·status 기준 조회 |
| `import_channel_history()` | 봇 시작 시 기존 메시지 수집 |
| `send_daily_summary()` | 매일 오전 AI 요약 전송 |
| `ai_weekly_cleanup()` | 매주 완료 후보 제안 |

---

## ⚠️ 파일 편집 시 인코딩 주의사항 (필독)

### 문제

Windows PowerShell 5.1은 기본적으로 파일을 **CP949(EUC-KR)**로 읽는다. 한글이 포함된 UTF-8 파일을 PowerShell의 `Get-Content` / `Set-Content`로 읽고 쓰면 한글이 전부 깨진다.

### 규칙

- **한글이 포함된 파일은 Claude의 Edit/Write 도구를 우선 사용**한다.
- PowerShell로 파일 내용을 읽고 써야 할 때는 반드시 명시적 UTF-8 인코딩 지정:

```powershell
# 올바른 방법
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding $false))

# 절대 사용 금지
Get-Content $path          # CP949로 읽음
Set-Content $path $content # BOM 포함 UTF-8로 씀 → 한글 손상
```

---

## Environment Variables (`.env`)

```
DISCORD_TOKEN=...          # Discord 봇 토큰
OPENAI_API_KEY=...         # OpenAI API 키
SUMMARY_CHANNEL_ID=...     # 일일 요약 리포트 받을 채널 ID
```
