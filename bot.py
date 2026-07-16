import discord
from discord import app_commands
import os
import re
import base64
import asyncio
import sqlite3
import subprocess
from datetime import datetime, timedelta
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID"))

# --- 데일리 리포트(공개 개발 저널) 설정 ---
DEVLOG_REPO_PATH = os.getenv("DEVLOG_REPO_PATH", r"C:\Users\ghwn1\daily-report")
DEVLOG_REPO_URL = "https://github.com/tgnugul/daily-report"
# 클라우드(VM)에서 git push 인증용. 설정 시 http 헤더로 토큰 주입, 없으면 로컬 gh 자격증명 사용.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# 폴더명 -> 그 폴더에 종합할 Discord 채널 목록
DEVLOG_CATEGORIES = {
    "잡생각": ["잡생각"],
    "뽀시래기": ["뽀시래기", "뽀시래기-피드백"],
    "세미나": ["세미나"],
}
# 공개 리포에 절대 올라가면 안 되는 민감 정보 패턴 (결정론적 게이트)
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",                                    # OpenAI 키
    r"gh[posru]_[A-Za-z0-9]{20,}",                             # GitHub 토큰
    r"AKIA[0-9A-Z]{16}",                                       # AWS 액세스 키
    r"xox[baprs]-[A-Za-z0-9-]{10,}",                           # Slack 토큰
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",                     # 개인 키
    r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S{6,}",  # 자격증명 할당
]

client_ai = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
scheduler = AsyncIOScheduler()


def init_db():
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT UNIQUE,
            channel TEXT,
            content TEXT,
            timestamp TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    # 기존 DB에 status 컬럼이 없으면 추가
    try:
        c.execute("ALTER TABLE messages ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def save_message(message_id, channel, content, timestamp=None):
    if not content.strip():
        return
    ts = timestamp or datetime.now().isoformat()
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO messages (message_id, channel, content, timestamp, status) VALUES (?, ?, ?, ?, 'active')",
        (str(message_id), channel, content, ts)
    )
    conn.commit()
    conn.close()


def mark_done(message_id):
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    c.execute("UPDATE messages SET status = 'done' WHERE message_id = ?", (str(message_id),))
    conn.commit()
    conn.close()
    return c.rowcount > 0


def get_all_messages(status="active"):
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    c.execute(
        "SELECT channel, content, timestamp FROM messages WHERE status = ? ORDER BY channel, timestamp",
        (status,)
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_channel_messages(channel_name, days=90, status="active"):
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    c.execute("""
        SELECT channel, content, timestamp FROM messages
        WHERE channel = ? AND status = ?
        AND timestamp >= datetime('now', ?)
        ORDER BY timestamp
    """, (channel_name, status, f'-{days} days'))
    rows = c.fetchall()
    conn.close()
    return rows


async def import_channel_history(guild):
    print("기존 채널 메시지 불러오는 중...")
    for channel in guild.text_channels:
        try:
            count = 0
            async for message in channel.history(limit=500):
                if message.author.bot:
                    continue
                # ✅ 반응이 이미 달린 메시지는 done으로 저장
                status = 'active'
                for reaction in message.reactions:
                    if str(reaction.emoji) == '✅':
                        status = 'done'
                        break
                conn = sqlite3.connect("memories.db")
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO messages (message_id, channel, content, timestamp, status) VALUES (?, ?, ?, ?, ?)",
                    (str(message.id), channel.name, message.content, message.created_at.isoformat(), status)
                )
                conn.commit()
                conn.close()
                count += 1
            if count:
                print(f"  #{channel.name}: {count}개 처리")
        except discord.Forbidden:
            print(f"  #{channel.name}: 접근 권한 없음 (스킵)")
    print("완료")


async def ai_weekly_cleanup():
    rows = get_all_messages(status="active")
    if not rows:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    text = "\n".join([f"[{r[0]}] {r[1]} (작성일: {r[2][:10]})" for r in rows])

    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    f"오늘 날짜는 {today}이야. "
                    "아래는 사용자의 메모 목록이야. "
                    "이미 지난 날짜의 일정이거나, 완료됐을 가능성이 높은 항목만 골라서 "
                    "완료 처리 대상 목록으로 알려줘. "
                    "확실하지 않으면 포함하지 마. "
                    "형식: 각 항목을 줄바꿈으로 구분해서 나열."
                )
            },
            {"role": "user", "content": text}
        ]
    )

    candidates = response.choices[0].message.content.strip()
    if not candidates:
        return

    channel = bot.get_channel(SUMMARY_CHANNEL_ID)
    if channel:
        await channel.send(
            f"**주간 정리 제안**\n\n"
            f"아래 항목들이 완료됐을 가능성이 높아요. "
            f"해당 메시지에 ✅ 반응을 달면 완료 처리됩니다.\n\n{candidates}"
        )


def get_messages_for_channels_on_date(channels, date_str):
    """지정한 채널들에서 특정 날짜(YYYY-MM-DD)에 작성된 메시지 조회 (status 무관).

    이미지/파일만 있는 빈 내용 메시지는 제외한다.
    """
    conn = sqlite3.connect("memories.db")
    c = conn.cursor()
    placeholders = ",".join("?" * len(channels))
    c.execute(
        f"""
        SELECT channel, content, timestamp FROM messages
        WHERE channel IN ({placeholders}) AND date(timestamp) = ?
        AND TRIM(content) != ''
        ORDER BY timestamp
        """,
        (*channels, date_str),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def scan_secrets(text):
    """민감 정보 패턴이 있으면 매칭된 조각(축약)을 반환, 없으면 None."""
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)[:40]
    return None


def reframe_devlog(folder, rows, date_str):
    """메모를 '의도만 충실히 요약한' 항목 목록 마크다운으로 정리."""
    text = "\n".join(f"[{r[0]}] {r[1]}" for r in rows)
    combined_note = (
        "이 카테고리는 '뽀시래기'(개발 기록)와 '뽀시래기-피드백'(사용자/테스트 피드백) "
        "두 채널의 메모를 하나의 목록으로 합쳐서 정리해. "
        if folder == "뽀시래기" else ""
    )
    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 사용자가 하루 동안 남긴 메모를 '충실하게 요약'하는 역할이야. "
                    "아래는 사용자가 남긴 메모야. 이 글은 GitHub 공개 포트폴리오에 올라가. "
                    f"{combined_note}"
                    "규칙: "
                    "(1) 사용자가 실제로 쓴 내용의 '의도'만 파악해서, 각 메모/생각을 "
                    "'- ' 불릿(또는 '1. 2. 3.' 번호) 목록으로 하나씩 표현해. "
                    "(2) 사용자가 쓰거나 생각하지 않은 감상·교훈·'배운 점'·느낀 점을 절대 지어내지 마. "
                    "메모에 없는 내용은 추가 금지. 과장·미화 금지. '배운 점' 같은 섹션도 넣지 마. "
                    "(3) API 키, 토큰, 비밀번호, 이메일/전화번호 등 개인 연락처, 그 외 민감 정보는 절대 포함하지 마. "
                    "(4) 출력은 마크다운. 맨 첫 줄은 '## {날짜}' 제목, 그 아래 목록만. "
                    "(5) 내용이 짧으면 짧게. 억지로 늘리지 마. 간결한 한국어로."
                ).replace("{날짜}", date_str),
            },
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content.strip() + "\n"


def _run_git(args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    # 토큰이 있으면(클라우드) http 헤더로 인증 주입. 로컬(토큰 없음)은 gh 자격증명 헬퍼 사용.
    auth = []
    if GITHUB_TOKEN:
        basic = base64.b64encode(f"x-access-token:{GITHUB_TOKEN}".encode()).decode()
        auth = ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]
    r = subprocess.run(
        ["git", "-C", DEVLOG_REPO_PATH, *auth, *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if r.returncode != 0:
        # 토큰이 로그에 남지 않도록 auth 옵션은 제외하고 메시지 구성
        raise RuntimeError(f"git {' '.join(args)} 실패: {(r.stderr or r.stdout).strip()}")
    return (r.stdout or "").strip()


def git_pull_rebase():
    _run_git(["pull", "--rebase", "origin", "main"])


def git_commit_push(date_str, folders):
    _run_git(["add", "-A"])
    _run_git(["commit", "-m", f"devlog: {date_str} ({', '.join(folders)})"])
    _run_git(["push", "origin", "main"])
    sha = _run_git(["rev-parse", "HEAD"])
    return f"{DEVLOG_REPO_URL}/commit/{sha}"


async def generate_and_commit_devlog(date_str=None):
    """전날(기본) 메모를 카테고리별로 정리해 daily-report 리포에 커밋."""
    target = date_str or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    notify = bot.get_channel(SUMMARY_CHANNEL_ID)
    try:
        await asyncio.to_thread(git_pull_rebase)
        committed = []
        for folder, channels in DEVLOG_CATEGORIES.items():
            rows = get_messages_for_channels_on_date(channels, target)
            if not rows:
                continue
            raw = "\n".join(r[1] for r in rows)
            md = await asyncio.to_thread(reframe_devlog, folder, rows, target)
            hit = scan_secrets(raw) or scan_secrets(md)
            if hit:
                if notify:
                    await notify.send(
                        f"⚠️ **[{folder}] {target} 데브로그 커밋 중단** — "
                        f"민감 정보 의심 패턴 발견(`{hit}`). 수동 확인 후 직접 올려주세요."
                    )
                continue
            path = os.path.join(DEVLOG_REPO_PATH, folder, f"{target}.md")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(md)
            committed.append(folder)

        if not committed:
            print(f"[devlog] {target}: 커밋할 내용 없음 (스킵)")
            return

        url = await asyncio.to_thread(git_commit_push, target, committed)
        if notify:
            await notify.send(
                f"✅ **{target} 데브 로그 커밋 완료**\n"
                f"카테고리: {', '.join(committed)}\n{url}"
            )
    except Exception as e:
        print(f"[devlog] 오류: {e}")
        if notify:
            await notify.send(f"❌ **{target} 데브 로그 자동 커밋 실패**\n```\n{e}\n```")


async def send_daily_summary():
    rows = get_all_messages(status="active")
    if not rows:
        return

    text = "\n".join([f"[{r[0]}] {r[1]} ({r[2][:10]})" for r in rows])
    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 사용자의 개인 비서야. "
                    "아래는 현재 진행 중인 메모들이야. "
                    "채널 이름이 카테고리 역할을 해. "
                    "채널별로 분류해서 핵심만 요약해줘. "
                    "일정이 있으면 날짜 순으로 정리해줘."
                )
            },
            {"role": "user", "content": text}
        ]
    )

    summary = response.choices[0].message.content
    channel = bot.get_channel(SUMMARY_CHANNEL_ID)
    if channel:
        await channel.send(f"**오늘의 요약**\n\n{summary}")


@tree.command(name="요약", description="현재 채널에 기록된 내용을 요약합니다")
@app_commands.describe(기간="요약할 기간 (일 수, 기본값 90)")
async def slash_summary(interaction: discord.Interaction, 기간: int = 90):
    await interaction.response.defer()
    rows = get_channel_messages(interaction.channel.name, days=기간)
    if not rows:
        await interaction.followup.send("이 채널에 저장된 내용이 없어요.")
        return

    text = "\n".join([f"{r[1]} ({r[2][:10]})" for r in rows])
    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": f"너는 개인 비서야. 아래는 '{interaction.channel.name}' 채널에 기록된 메모들이야. 핵심만 간결하게 요약해줘. 일정이 있으면 날짜 순으로 정리해줘."
            },
            {"role": "user", "content": text}
        ]
    )
    await interaction.followup.send(response.choices[0].message.content)


@tree.command(name="질문", description="저장된 모든 메모를 바탕으로 질문에 답합니다")
@app_commands.describe(내용="질문 내용을 입력하세요")
async def slash_question(interaction: discord.Interaction, 내용: str):
    await interaction.response.defer()
    rows = get_all_messages(status="active")
    if not rows:
        await interaction.followup.send("아직 저장된 메모가 없어요.")
        return

    text = "\n".join([f"[{r[0]}] {r[1]}" for r in rows])
    response = client_ai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 사용자의 개인 비서야. "
                    "아래는 사용자가 디스코드 채널에 기록한 모든 메모야. "
                    "채널 이름이 카테고리를 나타내. "
                    "사용자의 질문에 메모 내용을 바탕으로 정확하게 답해줘. "
                    "모르면 모른다고 해줘."
                )
            },
            {"role": "user", "content": f"메모 내용:\n{text}\n\n질문: {내용}"}
        ]
    )
    await interaction.followup.send(response.choices[0].message.content)


@tree.command(name="데브로그", description="지정 날짜의 데브 로그를 지금 생성해 GitHub에 커밋합니다")
@app_commands.describe(날짜="YYYY-MM-DD (비우면 어제)")
async def slash_devlog(interaction: discord.Interaction, 날짜: str = None):
    await interaction.response.defer()
    target = 날짜 or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    await interaction.followup.send(f"`{target}` 데브 로그 생성 중...")
    await generate_and_commit_devlog(target)


@bot.event
async def on_raw_reaction_add(payload):
    if str(payload.emoji) != '✅':
        return
    if payload.user_id == bot.user.id:
        return
    marked = mark_done(payload.message_id)
    if marked:
        print(f"완료 처리: message_id={payload.message_id}")


@bot.event
async def on_ready():
    init_db()
    print(f"봇 실행 중: {bot.user}")
    await tree.sync()
    print("슬래시 커맨드 등록 완료")
    for guild in bot.guilds:
        await import_channel_history(guild)
    if not scheduler.running:
        scheduler.add_job(send_daily_summary, "cron", hour=9, minute=0)
        scheduler.add_job(ai_weekly_cleanup, "cron", day_of_week="mon", hour=9, minute=10)
        scheduler.add_job(generate_and_commit_devlog, "cron", hour=6, minute=0)
        scheduler.start()


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    save_message(message.id, message.channel.name, message.content)
    print(f"[{message.channel.name}] {message.content}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
