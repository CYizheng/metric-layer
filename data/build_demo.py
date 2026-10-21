"""生成虚构演示库 data/demo.db。

为什么用生成器而不是手工造表：演示数据要可复现、可 review，
而且数据分布要能体现聚合查询的价值（有月份跨度、有分组基数）。
全部信息均为虚构，不来自任何真实投递记录。
"""

from __future__ import annotations

import os
import random
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "demo.db")

CITIES = ["成都", "贵阳", "广州", "北京", "上海", "深圳", "海口", "杭州", "西安", "武汉"]
CATEGORIES = ["数据分析", "数据治理", "算法工程", "大数据开发", "产品运营", "其他"]
CHANNELS = ["官网", "招聘平台", "内推", "校园宣讲", "猎头"]
STATUSES = ["流程中", "泡池子", "已挂", "已放弃"]
STATUS_W = [0.34, 0.30, 0.26, 0.10]
COMPANIES = [
    "东岭科技", "南湖数据", "青禾信息", "北辰智联", "云栖网络", "海川集团",
    "启明医疗", "长风物流", "瑞泽能源", "新宁银行", "格物智能", "远山传媒",
    "平湖制造", "澄明教育", "楚风重工", "朗润健康", "维桢咨询", "砺行汽车",
]
POSITIONS = ["数据分析师", "数据治理岗", "算法工程师", "大数据开发", "经营分析", "用户研究"]

SCHEMA = """
CREATE TABLE applications (
    id              INTEGER PRIMARY KEY,
    company         TEXT NOT NULL,
    position        TEXT NOT NULL,
    category        TEXT NOT NULL,
    city            TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL,
    applied_at      TEXT NOT NULL,
    next_action_at  TEXT,
    note            TEXT
);
CREATE TABLE events (
    id       INTEGER PRIMARY KEY,
    app_id   INTEGER NOT NULL,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    detail   TEXT,
    FOREIGN KEY (app_id) REFERENCES applications(id)
);
CREATE INDEX idx_app_city ON applications(city);
CREATE INDEX idx_app_status ON applications(status);
CREATE INDEX idx_ev_app ON events(app_id);
"""


def build(n: int = 128, seed: int = 20261021) -> str:
    rnd = random.Random(seed)
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)

    # 投递时间跨度：2026-08-01 起约 75 天，覆盖三个自然月
    for i in range(1, n + 1):
        day_offset = rnd.randint(0, 74)
        month = 8 + day_offset // 30
        day = day_offset % 30 + 1
        applied = f"2026-{month:02d}-{day:02d}"
        status = rnd.choices(STATUSES, weights=STATUS_W, k=1)[0]
        con.execute(
            "INSERT INTO applications (company, position, category, city, channel,"
            " status, applied_at, next_action_at, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                rnd.choice(COMPANIES) + f"（{i:03d}）",
                rnd.choice(POSITIONS),
                rnd.choice(CATEGORIES),
                rnd.choice(CITIES),
                rnd.choice(CHANNELS),
                status,
                applied,
                None if status in ("已挂", "已放弃") else f"2026-{month:02d}-{min(day + 7, 30):02d}",
                None,
            ),
        )

    # 事件流：投递必有；流程中的可能追加笔试、面试
    ev_id = 0
    for app_id in range(1, n + 1):
        row = con.execute(
            "SELECT status, applied_at FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        status, applied = row
        ev_id += 1
        con.execute(
            "INSERT INTO events (id, app_id, at, kind, detail) VALUES (?,?,?,?,?)",
            (ev_id, app_id, applied, "投递", "提交网申"),
        )
        if status == "流程中":
            for kind in ("笔试", "面试", "面试"):
                if rnd.random() < 0.55:
                    ev_id += 1
                    con.execute(
                        "INSERT INTO events (id, app_id, at, kind, detail)"
                        " VALUES (?,?,?,?,?)",
                        (ev_id, app_id, applied, kind, f"第 {ev_id} 场"),
                    )
        elif status == "已挂" and rnd.random() < 0.5:
            ev_id += 1
            con.execute(
                "INSERT INTO events (id, app_id, at, kind, detail) VALUES (?,?,?,?,?)",
                (ev_id, app_id, applied, "结果", "未通过"),
            )

    con.commit()
    rows = con.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    evs = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    con.close()
    return f"{rows} 条投递 / {evs} 条事件"


if __name__ == "__main__":
    print("已生成", DB)
    print(build())
