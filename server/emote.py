"""テキスト → 感情カテゴリの軽量分類器。

ぺけ子ちゃんが喋るテキストに含まれるキーワード/語尾から、口パク中に
使う表情を切り替えるためのヒントを返す。LLM や音声分析を介さず、
シンプルな部分一致だけで判定する。

返値はファーム側の face_map.h と握った 1 つのトークン文字列で、
ASCII 英小文字 + ハイフンのみ (HTTP ヘッダで URL エンコードなしに送れる)。

カテゴリと意図:
    neutral     ふつう / 想定しないテキスト → 既定の口パク (微笑み / やる気)
    joy         嬉しい・はしゃぐ系                  → 笑顔系
    sad         悲しい・泣く系・寂しい               → 泣き系
    embarrassed 困った・あやまる系                   → 困り系
    confused    わからない・迷い系                   → 拗ね・はてな系
    surprised   驚き・感動系                         → 驚き・キラキラ系
    sleepy      眠い・あくび系                       → あくび・就寝系
    confident   大丈夫・まかせて系                   → 自信・柔らか系
    angry       怒り・強い否定系                     → 怒り系
    panic       慌て・混乱系                         → 慌て系
    shy         照れ・恥ずかしさ系                   → 照れ系
    mischief    いたずら・冗談系                     → いたずら系
    relieved    安心・完了系                         → 安堵系
    cold        そっけない・冷静系                   → 冷淡系

ユーザー褒め反応:
    user_text に褒め語があれば、bot_text の内容に関わらず表情を上書きする。
    - 通常の褒め → embarrassed (はにかむ)
    - 強い褒め (大好き/ずっと一緒/抱きしめて) → shy (照れ)
"""

from __future__ import annotations

# 順序が大事: 上から順にマッチを取る。
# 「意図がはっきりした語」を先に、汎用的に拾える語 (joy の X68 など) を最後に置く。
# どれもヒットしなければ "neutral"。
_RULES: list[tuple[str, tuple[str, ...]]] = [
    # 困り・謝罪 — 直接的な表明なので最優先
    ("embarrassed", (
        "ごめん", "すまない", "申し訳", "もうしわけ",
        "失礼", "あちゃ", "やっちゃった",
        "間違えた", "まちがえた", "やらかした", "勘違い",
    )),
    # 慌て・混乱
    ("panic", (
        "あわわ", "わたわた", "パニック", "たいへん", "大変",
        "どうしよう", "しまった", "まずい", "やばい",
        "焦る", "あせる", "焦った", "あせった",
    )),
    # 怒り・強い否定
    ("angry", (
        "怒", "おこ", "ぷん", "許さない", "だめ", "ダメ",
        "やめて", "違うって", "ちがうって",
        "ひどい", "ひどいよ", "許せない", "むかつく",
    )),
    # 困惑・迷い — 「えっと」フィラーを surprised の「えっ」より先に拾う
    ("confused", (
        "わからない", "分からない", "わかんない", "うーん", "うーーん",
        "むずかしい", "難しい", "えーと", "えっと", "はてな",
        "どっち", "どちら", "迷う", "まよ", "ちがうかな",
    )),
    # 自信・励まし — joy の「ばっちり」より先に「まかせて/大丈夫」を勝たせる
    ("confident", (
        "まかせて", "任せて", "だいじょうぶ", "大丈夫", "もちろん",
        "やってみる", "がんばる", "頑張る", "きっと",
        "任せる", "まかせ", "いける", "できる",
    )),
    # 安堵・完了
    ("relieved", (
        "よかった", "良かった", "ほっと", "安心", "ひと安心",
        "終わった", "解決", "間に合った",
        "すっきり", "ほっとした", "助かった",
    )),
    # 照れ — ここが一番豊かに。「えへへ」「うふふ」等の笑い語も拾う
    ("shy", (
        "照れ", "てれる", "てれちゃう", "恥ずかしい", "はずかしい",
        "もじもじ", "内緒", "ないしょ",
        "えへへ", "うふふ", "えへへへ", "きゃあ",
        "ちょっと待って", "ちょっと、",
    )),
    # いたずら・冗談
    ("mischief", (
        "いたずら", "イタズラ", "冗談", "じょうだん", "ふふん",
        "にやり", "こっそり", "秘密", "ひみつ",
        "ひみつだよ", "だまされた", "ざまあ",
    )),
    # そっけない・冷静
    ("cold", (
        "別に", "べつに", "知らない", "しらない", "ふーん",
        "無関係", "冷静", "淡々", "それだけ",
        "どうでもいい", "興味ない", "どうでもいいよ",
    )),
    # 眠気
    ("sleepy", (
        "ねむい", "眠い", "あくび", "ふぁ〜", "ふぁー", "ぐぅ", "おやすみ",
        "ねむいな", "まぶた", "重い", "眠たく",
        "おやすみなさい", "すう", "すぅ",
    )),
    # 悲しみ — 寂しさ・孤独感・後悔など幅広く
    ("sad", (
        "かなしい", "悲しい", "つらい", "辛い", "泣き", "しょんぼり",
        "がっかり", "寂しい", "さみしい", "淋しい",
        "ひとり", "一人", "ぽつん", "おれさま",
        "もう会えない", "いなくなったら",
    )),
    # 驚き — 「なんと」は「なんとかなる」と紛らわしいので採用しない。
    # 「えっ」は「えっと」とも衝突するため confused の後に置いてもまだ広いが、
    # 「えええ」など強い形を優先。
    ("surprised", (
        # 「えっ」単独は「えっと」と衝突するので、句読点 / 終止形セットで判定
        "えっ、", "えっ。", "えっ!", "えっ!", "えっ?", "えっ?",
        "えええ", "まじで", "ほんと", "本当に", "すごい", "すっごい",
        "やば", "びっくり", "わぉ", "おお、",
        "まさか", "まさかの", "信じられない", "信じらんない",
    )),
    # 喜び — 範囲が広いので最後 (X68 系のメンションを拾う)
    ("joy", (
        "やったー", "やった", "うれしい", "嬉しい", "わーい", "わあい",
        "たのしい", "楽しい", "好き", "大好き", "最高", "ばっちり",
        "X68", "x68", "Human68k", "human68k", "x68000", "X68000",
        "かわいい", "可愛い", "カワイイ",
        "だいすき", "大すき", "ずっと一緒",
    )),
]

VALID_CATEGORIES: tuple[str, ...] = (
    "neutral",
    "joy",
    "sad",
    "embarrassed",
    "confused",
    "surprised",
    "sleepy",
    "confident",
    "angry",
    "panic",
    "shy",
    "mischief",
    "relieved",
    "cold",
)

_IRODORI_EMOJI_BY_CATEGORY: dict[str, str] = {
    "joy": "😆",
    "sad": "😭",
    "embarrassed": "🫣",
    "confused": "🤔",
    "surprised": "😲",
    "sleepy": "😪",
    "confident": "😊",
    "angry": "😠",
    "panic": "😰",
    "shy": "🫣",
    "mischief": "😏",
    "relieved": "😌",
    "cold": "🙄",
}


def irodori_emoji_for(category: str) -> str:
    """Irodori-TTS の絵文字スタイル制御に使う prefix を返す。"""
    return _IRODORI_EMOJI_BY_CATEGORY.get(category, "")


def with_irodori_emoji(text: str, category: str) -> str:
    """表示用テキストは変えず、TTS 入力だけ感情絵文字つきにする。"""
    emoji = irodori_emoji_for(category)
    if not emoji or not text:
        return text
    stripped = text.lstrip()
    if stripped.startswith(emoji):
        return text
    return f"{emoji} {text}"


def classify(text: str) -> str:
    """text を 1 つの感情カテゴリにマップする。未マッチなら 'neutral'。"""
    if not text:
        return "neutral"
    lowered = text.lower()  # ascii の大小差を吸収 (日本語には影響なし)
    for category, keywords in _RULES:
        for kw in keywords:
            if kw.lower() in lowered:
                return category
    return "neutral"


# 「ユーザがぺけ子ちゃんを褒めた」を拾うためのキーワード。
# bot 自身が言うのではなく、user_text にこれらが現れたら次の応答は
# 「はにかむ」= embarrassed / shy として表情を上書きする。
_PRAISE_KEYWORDS: tuple[str, ...] = (
    # 外見・可愛さ
    "かわいい", "可愛い", "カワイイ", "きれい", "綺麗", "キラキラ",
    # 能力・性格
    "えらい", "偉い", "すごい", "すっごい", "凄い", "スゴい",
    "賢い", "かしこい", "頭いい", "かしこいね",
    # 好意
    "好きだよ", "好きだ", "大好き", "だいすき", "大すき",
    "ずっと一緒", "ずっとそばに", "離さない",
    # 感謝
    "ありがとう", "ありがと", "助かった", "助かる", "感謝",
    # おねだり・甘え
    "なでて", "撫でて", "抱きしめて", "ぎゅっとして",
    "もう一回", "もういちど",
    # その他褒め
    "上手", "じょうず", "うまい", "うまかった", "天才", "てんさい",
    "さすが", "パリピ", "完璧", "かんぺき",
)


def is_praise(user_text: str) -> bool:
    """user_text に褒め系の語が含まれているか。"""
    if not user_text:
        return False
    lowered = user_text.lower()
    return any(kw.lower() in lowered for kw in _PRAISE_KEYWORDS)


# 強い褒め: ここが当たると shy (照れ) に倒す。弱い褒めは embarrassed のまま。
_STRONG_PRAISE: tuple[str, ...] = (
    "大好き", "だいすき", "ずっと一緒", "離さない",
    "抱きしめて", "ぎゅっとして",
    "最高", "完璧", "天才",
)


def is_strong_praise(user_text: str) -> bool:
    """user_text に強い好意・愛情表現があるか。"""
    if not user_text:
        return False
    lowered = user_text.lower()
    return any(kw.lower() in lowered for kw in _STRONG_PRAISE)


def classify_reaction(user_text: str, bot_text: str) -> str:
    """ユーザ発話とぺけ子応答の両方を見て表情カテゴリを決める。

    判定ロジック:
    1. ユーザ発話に強い褒め (大好き/ずっと一緒/抱きしめて) → "shy" (照れ)
    2. ユーザ発話に通常の褒め → "embarrassed" (はにかむ)
    3. それ以外 → bot_text の内容から通常分類
    """
    if is_praise(user_text):
        if is_strong_praise(user_text):
            return "shy"
        return "embarrassed"
    return classify(bot_text)
