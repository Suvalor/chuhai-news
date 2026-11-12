"""本地演示数据：设计稿里的虚构文章与作者，仅用于预览样式，不是真实内容。
用法：.venv/bin/python scripts/seed_demo.py   （写入 CHUHAI_DB 指向的库，默认 data/chuhai.db）"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.ingest import normalize  # noqa: E402

AUTHORS = {
    "maralin": ("Mara Lin", "独立顾问，写定价、定位和时间管理。", "Independent advisor writing on pricing, positioning and time."),
    "devbuilds": ("Dev Okafor", "工程师，记录自己真实在用的 AI 工作流与工具。", "Engineer documenting the AI workflows and tools he actually uses."),
    "yizhou_wang": ("王一舟", "独立开发者，一个人做出海 SaaS。", "Solo founder building a global SaaS."),
    "theobrandt": ("Theo Brandt", "写决策、偏差和反直觉的常识。", "Writes about decisions, bias and counter-intuitive common sense."),
    "sanaships": ("Sana Iqbal", "LLM 产品负责人，关注 eval 与推理成本。", "LLM product lead focused on evals and inference cost."),
    "lenapark": ("Lena Park", "增长负责人，带过三个产品进入日韩和拉美市场。", "Growth lead who has taken three products into Asia and LatAm."),
    "xiaoman_lin": ("林小满", "长文作者，写作与个人品牌。", "Long-form writer on writing and personal brand."),
}

# (id, handle, cat, lang, days, mins, views, likes, saves, title, title_t, paras[(orig, trans)])
ARTICLES = [
    ("a1", "maralin", "cog", "en", 2, 9, 48200, 3120, 2210, "The Compounding Calendar: Why I Stopped Saying Yes", "复利日历：我为什么不再轻易答应别人", [
        ("For years I treated my calendar like an inbox: whoever arrived first got a slot. The result was a week full of small, reasonable commitments and no room for anything that compounds.", "很多年里，我把日历当收件箱用：谁先来，谁就占一个时段。结果是一周塞满了合理又琐碎的承诺，却没有任何能产生复利的空间。"),
        ("The fix was not discipline. It was a rule: two four-hour blocks per week are pre-sold to my future self. Anything else has to fit around them, not inside them.", "解决办法不是自律，而是一条规则：每周两个四小时的时间块，提前「卖」给未来的自己。其他事只能绕开它们，不能挤进去。"),
        ("Six months later, those eight hours produced more than the other forty combined. Not because I worked harder in them, but because nothing interrupted the work long enough to make it good.", "六个月后，这八小时的产出超过了其余四十小时的总和。不是因为我在里面更努力，而是因为没有任何打断，工作才有机会变好。"),
        ("Saying no got easier once I could point to the block. People respect a prior commitment far more than they respect a preference.", "当我能指着那个时间块说「已经有安排」时，拒绝变得容易多了。人们尊重既定承诺，远胜于尊重个人偏好。")]),
    ("a6", "yizhou_wang", "global", "zh", 1, 14, 61500, 4380, 3960, "一个人做出海 SaaS 的第一年：从 0 到月入 3 万美金", "Year One of a Solo Global SaaS: From Zero to $30K MRR", [
        ("去年这个时候，我辞职，手里只有一个想法和六个月的存款。今天产品月收入三万美金，没有融资，没有员工。", "This time last year I quit with one idea and six months of savings. Today the product makes $30K a month, with no funding and no employees."),
        ("前三个月几乎全部花在找人聊天上。我在 X 上私信了两百多位潜在用户，真正回复的四十个人，后来成了第一批付费客户。", "The first three months went almost entirely to conversations. I DMed over two hundred potential users on X; the forty who replied became my first paying customers."),
        ("最大的教训：不要为中文市场做完再翻译。从第一天起就用英文写文档、定价用美元，你的产品会被另一种标准要求，也会长成另一种样子。", "The biggest lesson: don’t build for the Chinese market and translate later. Write docs in English and price in dollars from day one. The product gets held to a different standard, and grows into a different shape.")]),
    ("a4", "devbuilds", "ai", "en", 3, 11, 39800, 2650, 2890, "I Replaced My Research Workflow with Three Agents", "我用三个 Agent 替换了整个调研流程", [
        ("My old research process was a browser with forty tabs and a doc I never finished. Now it is three small agents with narrow jobs and one shared notes file.", "我以前的调研流程是开着四十个标签页的浏览器，外加一份永远写不完的文档。现在它是三个分工明确的小 Agent 和一个共享笔记文件。"),
        ("The scout finds sources and rejects anything older than a year. The reader extracts claims with citations. The critic tries to break each claim before it reaches me.", "侦察员负责找来源，并剔除一年以前的内容；阅读员提取论点并附上出处；审查员在论点到达我手上之前，先尝试推翻它。"),
        ("The critic is the one that matters. Without it, the system confidently produces summaries of things that are not true.", "真正关键的是审查员。没有它，整个系统会非常自信地总结出一堆并不真实的东西。"),
        ("Total setup time was an afternoon. The real cost was learning to write job descriptions for software.", "搭建总共只花了一个下午。真正的成本，是学会给软件写岗位说明。")]),
    ("a2", "maralin", "money", "en", 6, 8, 33400, 2010, 2480, "Pricing Is a Positioning Decision, Not a Math Problem", "定价是定位问题，不是数学题", [
        ("Most founders price by looking at costs and competitors, then shaving ten percent. That tells customers exactly one thing: you are the cheaper version of someone else.", "大多数创始人定价时看成本、看竞品，然后再便宜一成。这只向客户传达了一件事：你是别人的廉价版。"),
        ("Price is the first sentence of your positioning. Before you choose a number, choose the alternative you want to be compared against — a hire, an agency, a lost quarter.", "价格是定位的第一句话。在选数字之前，先选你想被拿来比较的对象——一个员工、一家外包公司，或者被浪费的一个季度。"),
        ("When we reframed our product against a part-time analyst instead of other software, we tripled the price and conversion went up.", "当我们把产品的参照物从同类软件换成一名兼职分析师后，价格翻了三倍，转化率反而上升了。")]),
    ("a11", "lenapark", "global", "en", 2, 10, 29700, 2240, 2610, "Localization Is Not Translation", "本地化不是翻译", [
        ("We launched in Japan with a perfectly translated site and got almost no signups. The words were right; everything around them was wrong.", "我们带着翻译得无可挑剔的网站进入日本，几乎没有人注册。字都对，字周围的一切都错了。"),
        ("Japanese buyers wanted a company address, a phone number and case studies from local firms before they would even start a trial.", "日本客户在开始试用之前，就想看到公司地址、电话号码和本地企业的案例。"),
        ("We rebuilt the landing page around trust instead of features. Signups went up fourfold without changing a single line of product.", "我们把落地页从「讲功能」改成「讲信任」。产品一行代码没改，注册量涨了四倍。")]),
    ("a9", "sanaships", "ai", "en", 4, 10, 27600, 1880, 2240, "Evals Are the New Unit Tests", "Eval 就是新时代的单元测试", [
        ("Every team shipping with language models eventually hits the same wall: a prompt change fixes one case and silently breaks five others.", "每个用大模型做产品的团队，最终都会撞上同一堵墙：改一句提示词修好了一个案例，却悄悄搞坏了另外五个。"),
        ("The fix is boring and effective. Keep a file of real inputs with expected properties, run it on every change, and refuse to merge when the score drops.", "解决方法无聊但有效：保存一份真实输入及其期望特征的文件，每次改动都跑一遍，分数下降就拒绝合并。"),
        ("Start with twenty cases you pulled from production logs. That alone will catch more regressions than any amount of clever prompting.", "先从生产日志里挑二十个案例开始。光是这一步，就能比任何巧妙的提示词技巧拦住更多的退化。")]),
    ("a12", "xiaoman_lin", "create", "zh", 3, 7, 25300, 2080, 2350, "长文写作，最重要的是删", "In Long-Form Writing, Cutting Is Everything", [
        ("我的每篇长文，初稿都在一万字左右，最后发出来的不到四千。被删掉的，往往是我最舍不得的部分。", "Every long piece I write starts around ten thousand characters and ships under four thousand. What gets cut is usually what I loved most."),
        ("判断标准只有一个：如果读者跳过这一段，会不会错过什么？不会，就删。", "There is only one test: if the reader skipped this paragraph, would they miss anything? If not, cut it."),
        ("删完之后，再大声读一遍。读着卡住的地方，就是读者会离开的地方。", "After cutting, read it aloud. Wherever you stumble is where readers will leave.")]),
    ("a8", "theobrandt", "cog", "en", 9, 7, 41200, 2930, 1640, "Most Advice Is Survivorship Bias With Good Lighting", "大多数建议，只是打光很好的幸存者偏差", [
        ("When someone successful tells you what worked, they are describing one path through a maze they cannot see the rest of.", "当一个成功的人告诉你什么方法有效时，他描述的只是迷宫中的一条路，而迷宫的其余部分他自己也看不见。"),
        ("The useful question is never “what did you do?” It is “what did you try that failed, and how would I know if I were about to make the same mistake?”", "真正有用的问题从来不是「你做了什么」，而是「你试过哪些失败的做法？我怎样才能知道自己正要犯同样的错？」"),
        ("Advice that comes with its own failure conditions is rare. Treat it like gold.", "附带失败条件的建议非常罕见。遇到了，就把它当金子。")]),
    ("a5", "devbuilds", "tool", "en", 5, 12, 36900, 1720, 3310, "The 14 Tools I Actually Pay For in 2026", "2026 年我真正付费的 14 个工具", [
        ("Every year I cancel everything and re-subscribe only to what I missed within two weeks. This year fourteen survived.", "每年我都会退订所有服务，只把两周内真正想念的重新订回来。今年留下了十四个。"),
        ("The pattern is clear: I pay for things that remove a decision, not for things that add a feature. A good tool makes me think less about the tool.", "规律很明显：我愿意为「帮我省掉一个决定」付费，而不是为「多一个功能」付费。好工具让我更少地想起工具本身。"),
        ("The full list, with what each replaced and what it costs per month, is below. Nothing here is sponsored.", "完整清单在下面，包括每个工具替换了什么、每月花多少钱。没有任何赞助。")]),
    ("a13", "lenapark", "money", "en", 8, 9, 21900, 1460, 1930, "Pricing in Five Currencies Without Losing Your Mind", "五种货币定价，如何不把自己逼疯", [
        ("The first time we added local currencies, we simply converted our dollar price. Customers in Brazil and India saw numbers that felt arbitrary and expensive.", "第一次支持本地货币时，我们只是把美元价格直接换算过去。巴西和印度的客户看到的数字，既随意又昂贵。"),
        ("Now each market gets a price that ends in a round local number and reflects local purchasing power. We review them once a quarter, not once a week.", "现在每个市场的价格都是本地的整数，并参考当地购买力。我们每季度复盘一次，而不是每周。"),
        ("Revenue per visitor went up in every region, including the ones where the price went down.", "每个地区的单访客收入都上升了，包括那些降了价的地区。")]),
    ("a7", "yizhou_wang", "cog", "zh", 12, 6, 22800, 1540, 1980, "我用来筛选需求的五个问题", "Five Questions I Use to Filter Ideas", [
        ("每周我都会收到很多「你应该做个 XX」的建议。为了不被它们淹没，我只用五个问题过滤。", "Every week I get plenty of “you should build X” suggestions. To avoid drowning in them, I filter with just five questions."),
        ("第一个问题最狠：这个人上一次为解决这个问题付钱是什么时候？答不上来的，直接放弃。", "The first is the harshest: when did this person last pay to solve this problem? If there’s no answer, drop it."),
        ("剩下四个分别关于频率、替代方案、触达渠道和我自己是否愿意用三年。五个都过的点子，一年里大概只有两三个。", "The other four cover frequency, alternatives, distribution, and whether I’d want to work on it for three years. Maybe two or three ideas a year pass all five.")]),
    ("a14", "xiaoman_lin", "create", "zh", 10, 6, 18600, 1390, 1720, "我是怎么在 X 上写 Thread 的", "How I Write Threads on X", [
        ("一条好 Thread 的第一句话，只做一件事：让人想看第二句。", "The first line of a good thread has one job: make people want to read the second."),
        ("我会先写结论，再倒着补论据。这样每一条都在为结论服务，没有一句是闲笔。", "I write the conclusion first, then work backward to the evidence. Every post serves the conclusion; nothing is filler."),
        ("最后一条永远留给原文链接和一句真诚的邀请，而不是「求转发」。", "The last post always holds the link and a sincere invitation — never a plea for retweets.")]),
    ("a10", "sanaships", "tool", "en", 15, 9, 19400, 1210, 2050, "A Field Guide to Prompt Caching", "提示词缓存实战指南", [
        ("Prompt caching is the cheapest performance win most teams are leaving on the table. If your system prompt is long and stable, you are paying for it on every call.", "提示词缓存是大多数团队都忽略了的、成本最低的性能优化。如果你的系统提示词又长又稳定，你每次调用都在为它重复付费。"),
        ("Put everything static at the front: instructions, tool definitions, reference documents. Put everything that changes at the end.", "把所有静态内容放在最前面：指令、工具定义、参考文档；把所有会变化的内容放在最后。"),
        ("In our app, that reordering alone cut latency by a third and the monthly bill by more than half.", "在我们的应用里，仅仅调整顺序，就让延迟降低了三分之一，月度账单减少了一半以上。")]),
    ("a3", "maralin", "cog", "en", 20, 5, 15800, 1360, 1120, "Notes on Taste", "关于品味的几条笔记", [
        ("Taste is not a gift. It is the residue of having paid close attention to a lot of things, and having been wrong about many of them out loud.", "品味不是天赋。它是你认真观察过大量事物、并且公开地错过很多次之后留下的沉淀。"),
        ("The fastest way to develop it is to copy something you admire by hand, then write down every decision you didn’t understand.", "培养品味最快的方法，是亲手临摹一件你欣赏的作品，然后把每一个你没看懂的决定写下来。"),
        ("Taste without output becomes snobbery. Output without taste becomes noise. The work is keeping both in the same room.", "只有品味没有产出，会变成清高；只有产出没有品味，会变成噪音。真正的功夫是让两者共处一室。")]),
]


def payloads():
    now = datetime.now(timezone.utc)
    for id_, h, cat, lang, days, mins, *_counts, title, title_t, paras in ARTICLES:
        name, bz, be = AUTHORS[h]
        yield {"external_id": f"demo:{id_}", "source_url": f"https://x.com/{h}", "lang": lang, "category": cat,
               "title": title, "title_translated": title_t, "mins": mins,
               "published_at": (now - timedelta(days=days, hours=2)).isoformat(),
               "author": {"handle": h, "name": name, "bio_zh": bz, "bio_en": be},
               "paragraphs": [{"text": o, "translation": t} for o, t in paras]}, _counts


def main():
    db.init()
    for p, (views, likes, saves) in payloads():
        slug, _ = db.upsert_article(normalize(p))
        with db.connect() as con:
            con.execute("UPDATE articles SET views=?, likes=?, saves=? WHERE slug=?", (views, likes, saves, slug))
    print(f"已写入 {len(ARTICLES)} 篇演示文章到 {db.DB_PATH}")


if __name__ == "__main__":
    main()
