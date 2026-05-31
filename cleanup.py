import sqlite3

conn = sqlite3.connect("memories.db")
c = conn.cursor()
c.execute("DELETE FROM messages WHERE id NOT IN (SELECT MIN(id) FROM messages GROUP BY channel, content)")
conn.commit()
print("중복 제거 완료:", c.rowcount, "개 삭제")
conn.close()
