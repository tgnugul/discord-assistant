import discord
from discord import app_commands
import os
import sqlite3
from datetime import datetime
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID"))

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
        scheduler.start()


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    save_message(message.id, message.channel.name, message.content)
    print(f"[{message.channel.name}] {message.content}")


bot.run(DISCORD_TOKEN)
