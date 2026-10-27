"""Rule-based finance classifier for announcements, policy news and limit-up reasons.

The rules are deliberately transparent and cheap: a backtest over six months needs hundreds of thousands of
headlines, and an LLM call per headline would be slow and expensive. `SnowNLP` was tested and rejected (it scored
"股东拟减持" as mildly positive and "签订10亿元大单" as negative); the finance lexicon below is the default.
`llm_classify.py` shows how to route a sample through the existing `quant/ml/llm` DeepSeek pipeline instead.

Nothing here is fitted to the backtest; the weights are ordinal and fixed before looking at returns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

POSITIVE = [
    (r"中标|中选|成交|订单|合同|框架协议|战略合作|合作协议|签订|签署", "major_event", 1.0),
    (r"收购|重组|资产注入|控制权变更|要约收购|股权转让|借壳|资产置换", "major_event", 1.0),
    (r"增持|回购|股权激励|员工持股", "holding", 0.7),
    (r"预增|预盈|扭亏|净利润增长|营收增长|业绩超预期|同比增长", "earnings", 0.9),
    (r"获批|注册|临床|专利|新产品|投产|量产|涨价|提价|扩产", "major_event", 0.8),
    (r"战略|合作|投资|新建|增资|对外投资|中标候选人", "major_event", 0.5),
]
NEGATIVE = [
    (r"减持|清仓|拟减持|减持计划", "holding", -0.8),
    (r"立案|调查|处罚|违规|警示函|监管函|问询函|关注函|纪律处分", "risk", -1.0),
    (r"终止|失败|取消|中止|诉讼|仲裁|冻结|查封", "risk", -0.9),
    (r"预减|预亏|亏损|商誉减值|计提|下修|业绩下降|同比下降", "earnings", -0.9),
    (r"退市|风险提示|澄清|辟谣|异常波动", "risk", -0.6),
    (r"质押|担保|逾期|债务|资金占用", "risk", -0.5),
]
ROUTINE = re.compile(
    r"股东大会|董事会|监事会|独立董事|审计|年报|季报|半年报|募集说明书|保荐|持续督导|"
    r"投资者关系|调研|章程|制度|议事规则|管理办法|人员变动|换届"
)

THEME_KEYWORDS = {
    "人工智能": r"人工智能|AI|大模型|算力|智能体|机器人|具身智能|数据中心",
    "半导体": r"半导体|芯片|集成电路|光刻|存储|先进封装|晶圆",
    "商业航天": r"商业航天|卫星|火箭|低空经济|无人机|eVTOL",
    "新能源": r"光伏|风电|储能|固态电池|锂电|钠电|氢能|充电桩|电网",
    "汽车": r"新能源汽车|智能驾驶|自动驾驶|汽车|整车|零部件|车路云",
    "医药": r"创新药|医药|医疗|生物|临床|疫苗|中药|器械|CXO",
    "军工": r"军工|国防|航天|航空|导弹|雷达|军贸",
    "地产": r"房地产|地产|城中村|保障房|房价|收储|城市更新",
    "金融": r"券商|证券|保险|银行|资本市场|降准|降息|并购重组|市值管理",
    "消费": r"消费|旅游|免税|食品|白酒|零售|育儿|生育|养老|服务消费",
    "数据要素": r"数据要素|数字经济|数据资产|信创|网络安全|东数西算",
    "电力": r"电力|电网|核电|水电|虚拟电厂|特高压",
    "有色": r"稀土|铜|铝|黄金|锂|钴|镍|小金属",
    "化工": r"化工|农药|化肥|涨价|化纤|新材料",
    "传媒": r"游戏|影视|传媒|出版|短剧|IP|文化",
    "农业": r"农业|种业|养殖|猪肉|粮食|转基因",
}
THEME_TO_SW2 = {
    "人工智能": {"计算机", "通信", "电子", "机械设备"},
    "半导体": {"电子"},
    "商业航天": {"国防军工", "通信", "机械设备"},
    "新能源": {"电力设备", "有色金属"},
    "汽车": {"汽车", "机械设备", "电子"},
    "医药": {"医药生物"},
    "军工": {"国防军工"},
    "地产": {"房地产", "建筑装饰", "建筑材料"},
    "金融": {"非银金融", "银行"},
    "消费": {"商贸零售", "食品饮料", "社会服务", "纺织服饰", "家用电器"},
    "数据要素": {"计算机", "通信", "传媒"},
    "电力": {"公用事业", "电力设备"},
    "有色": {"有色金属"},
    "化工": {"基础化工", "石油石化"},
    "传媒": {"传媒", "计算机"},
    "农业": {"农林牧渔"},
}
POLICY_POSITIVE = re.compile(r"支持|加快|推动|促进|鼓励|批复|规划|试点|补贴|减税|降准|降息|宽松|重大|突破|创新|发展|建设|开工|扩内需|提振")
POLICY_NEGATIVE = re.compile(r"限制|整顿|收紧|监管|处罚|风险|下滑|衰退|禁止|严控|去产能")

_POS = [(re.compile(p), cat, w) for p, cat, w in POSITIVE]
_NEG = [(re.compile(p), cat, w) for p, cat, w in NEGATIVE]
_THEMES = {k: re.compile(v) for k, v in THEME_KEYWORDS.items()}


@dataclass
class Signal:
    sentiment: str = "neutral"
    score: float = 0.0
    category: str = "other"
    themes: list[str] = field(default_factory=list)
    confidence: float = 0.0


def classify_announcement(title: str, typ: str = "") -> Signal:
    text = f"{typ} {title}"
    if ROUTINE.search(text) and not any(p.search(title) for p, _, _ in _POS + _NEG):
        return Signal(confidence=0.2)
    neg = [(w, cat, p) for p, cat, w in _NEG if p.search(title)]
    if neg:
        w, cat, _ = min(neg, key=lambda x: x[0])
        return Signal("bearish", w, cat, confidence=0.75)
    pos = [(w, cat, p) for p, cat, w in _POS if p.search(title)]
    if pos:
        w, cat, _ = max(pos, key=lambda x: x[0])
        return Signal("bullish", w, cat, confidence=0.7)
    return Signal()


def classify_policy(title: str, content: str = "") -> Signal:
    text = f"{title} {content}"
    themes = [name for name, pat in _THEMES.items() if pat.search(text)]
    if not themes:
        return Signal()
    if POLICY_NEGATIVE.search(text) and not POLICY_POSITIVE.search(text):
        return Signal("bearish", -0.5, "policy", themes, confidence=0.55)
    score = 1.0 if POLICY_POSITIVE.search(text) else 0.3
    return Signal("bullish", score, "policy", themes, confidence=0.6)


def classify_market_news(title: str, content: str = "") -> Signal:
    sig = classify_policy(title, content)
    if sig.themes:
        return sig
    neg = any(p.search(title) for p, _, _ in _NEG)
    pos = any(p.search(title) for p, _, _ in _POS)
    if neg and not pos:
        return Signal("bearish", -0.5, "news", confidence=0.5)
    if pos and not neg:
        return Signal("bullish", 0.5, "news", confidence=0.5)
    return Signal()


def classify_limit_up_reason(reason: str) -> list[str]:
    tags = [x.strip() for x in re.split(r"[+＋、,，;；/]", reason or "") if x.strip()]
    return list(dict.fromkeys(tags))


def policy_industries(themes: list[str]) -> set[str]:
    out: set[str] = set()
    for t in themes:
        out |= THEME_TO_SW2.get(t, set())
    return out


def classify_announcements_frame(df):
    """Add sentiment/score/category columns to an announcements DataFrame (date, code, title, type)."""
    if df.empty:
        return df
    rows = [classify_announcement(str(t), str(y)) for t, y in zip(df["title"], df.get("type", ""))]
    out = df.copy()
    out["sentiment"] = [s.sentiment for s in rows]
    out["score"] = [s.score for s in rows]
    out["category"] = [s.category for s in rows]
    return out


def classify_policy_frame(df):
    if df.empty:
        return df
    rows = [classify_policy(str(t), str(c)) for t, c in zip(df["title"], df.get("content", ""))]
    out = df.copy()
    out["sentiment"] = [s.sentiment for s in rows]
    out["score"] = [s.score for s in rows]
    out["themes"] = [s.themes for s in rows]
    return out
