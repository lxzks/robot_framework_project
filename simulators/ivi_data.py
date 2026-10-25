"""
ivi_data.py — IVI 信息娱乐共享数据字典
========================================

CAN 报文只有 8 字节，歌名/街道名/联系人名放不进去。
真实车机的做法：**CAN 里传索引，GUI 侧按索引查表**。
本文件就是这套"查表"，仪表盘 GUI 和安卓车机 GUI 共用 —— 保证两块屏显示一致。
"""

# 歌单：(歌名, 歌手, 时长秒)
TRACKS = [
    ("海阔天空", "Beyond", 326),
    ("晴天", "周杰伦", 269),
    ("平凡之路", "朴树", 302),
    ("晚安", "颜人中", 243),
    ("起风了", "买辣椒也用券", 325),
    ("Successful." , "Ariana Grande", 231),
]

# 联系人：(姓名, 号码)
CONTACTS = [
    ("妈妈", "13812345678"),
    ("老王", "13998765432"),
    ("张经理", "13655556666"),
    ("李工", "13777778888"),
]

# 街道名
STREETS = [
    "中山大道",
    "解放大道路",
    "建设大道",
    "沿江大道",
    "二环线",
    "东风大道",
]

# 转向箭头文字（中文）
TURN_NAMES = {
    0: "", 1: "左转", 2: "右转", 3: "直行",
    4: "偏左", 5: "偏右", 6: "掉头", 7: "到达",
}


def track_info(index: int) -> tuple[str, str, int]:
    """按索引取歌曲，越界返回最后一首"""
    if 0 <= index < len(TRACKS):
        return TRACKS[index]
    return TRACKS[-1]


def contact_info(index: int) -> tuple[str, str]:
    if 0 <= index < len(CONTACTS):
        return CONTACTS[index]
    return ("未知号码", "00000000000")


def street_name(index: int) -> str:
    if 0 <= index < len(STREETS):
        return STREETS[index]
    return "未知道路"


def media_status_name(code: int) -> str:
    return {0: "停止", 1: "播放", 2: "暂停", 3: "切歌"}.get(code, "")


def call_status_name(code: int) -> str:
    return {0: "", 1: "来电", 2: "通话中"}.get(code, "")
