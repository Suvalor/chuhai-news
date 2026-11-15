"""界面文案（照搬设计稿 T 字典）。界面语言用 Cookie `ui` 切换，与文章原文/译文开关无关。"""

T = {
    "zh": dict(discover="发现", saved="收藏", search="搜索", searchPh="搜标题、作者、话题",
               mastKicker="chuhainews — X 长文精选 · 原文阅读 · 一键翻译", mh1="X 上值得读完的长文，", mh2a="按你想解决的",
               mh2b="问题", mh2c="排好。", indexLabel="按问题找", indexHint="你现在最想解决什么？", mustLabel="今日必读",
               authorsLabel="作者", authorsHint="跟着人读，比跟着话题读更快", startReading="开始阅读", read="阅读",
               readShort="阅读", like="赞", save="收藏", min="分钟", heatLabel="热度", hot="热度", latest="最新",
               views="阅读量", rankLabel="榜单", results="搜索结果", articlesOf="全部文章", empty="没有找到符合的文章。换个词试试。",
               back="返回", cont="继续读", sameAuthor="同作者", sameCat="同类目", onX="在 X 看原帖", transShort="译",
               viewOrig="看原文", next="下一篇", copyright="原文版权归作者所有 · chuhainews 仅整理与翻译 ·",
               followOnX="在 X 关注", articles="文章", totalViews="总阅读", totalLikes="获赞", savedTab="我的收藏",
               likedTab="我赞过的", savedEmpty="还没有收藏。读到好文章时按 S，它就会留在这里。", likedEmpty="还没有赞过的文章。",
               hotSearch="大家在搜", clearQ="清除", toc="目录", close="关闭",
               keys="← → 切换 · T 翻译 · L 赞 · S 收藏 · ESC 返回", transTo="AI 整篇翻译 · 原文为", langEn="英文",
               langZh="中文", origEn="英文原文", origZh="中文原文", short_en="EN", short_zh="中", upNext="接着读",
               flowMore="继续下滑，下一篇会自动接上", flowEnd="这一组读完了。换到「同类目」继续。", footA="只收录值得读完的长文",
               footB="原文版权归各自作者所有", footNav="页脚导航", footPublication="编辑部", footStandards="选文标准",
               footRights="版权与署名", footResources="订阅与资源", footFeed="RSS 订阅",
               tools="工具", excelRatio="Excel 比例分摊计算器",
               excelDesc="在线按比例分摊 Excel 金额、费用或其他数值。同名记录自动汇总，结果可复制回 Excel。表格内容在浏览器内处理。",
               authorsUnit="位作者", prev="上一页", nextPage="下一页", readSource="阅读原帖", introLabel="编者导读", about="关于", topics="专题",
               ring="值得读完的长文 · LONG READS WORTH FINISHING · ",
               siteDesc="chuhainews 精选 X 上值得读完的长文：原文阅读、一键中英翻译，按认知、挣钱、AI、工具、出海、创作六类问题整理。"),
    "en": dict(discover="Discover", saved="Saved", search="Search", searchPh="Titles, authors, topics",
               mastKicker="chuhainews — long reads from X · original text · one-tap translation",
               mh1="Long reads from X worth finishing,", mh2a="sorted by the ", mh2b="problem", mh2c=" you want to solve.",
               indexLabel="Find by problem", indexHint="What are you trying to solve?", mustLabel="Must read today",
               authorsLabel="Authors", authorsHint="Following people beats following topics", startReading="Start reading",
               read="Reads", readShort="reads", like="Like", save="Save", min="min", heatLabel="Heat", hot="Hot",
               latest="New", views="Most read", rankLabel="Ranking", results="Results", articlesOf="All articles",
               empty="Nothing matches. Try another word.", back="Back", cont="Keep reading", sameAuthor="Same author",
               sameCat="Same topic", onX="Open on X", transShort="Translate", viewOrig="View original", next="Next",
               copyright="Copyright belongs to the author · curated & translated by chuhainews ·", followOnX="Follow on X",
               articles="Articles", totalViews="Reads", totalLikes="Likes", savedTab="Saved", likedTab="Liked",
               savedEmpty="Nothing saved yet. Press S on a good read and it lands here.", likedEmpty="No liked articles yet.",
               hotSearch="Trending", clearQ="Clear", toc="Contents", close="Close",
               keys="← → SWITCH · T TRANSLATE · L LIKE · S SAVE · ESC BACK", transTo="Machine-translated · original in ",
               langEn="English", langZh="Chinese", origEn="Original EN", origZh="Original ZH", short_en="EN", short_zh="ZH",
               upNext="Up next", flowMore="Keep scrolling — the next one follows",
               flowEnd="That’s this set. Switch to “Same topic” to continue.", footA="Only long reads worth finishing",
               footB="Original articles © their respective authors.", footNav="Footer navigation", footPublication="Publication",
               footStandards="Editorial standards", footRights="Copyright & attribution", footResources="Resources",
               footFeed="RSS feed", authorsUnit="authors", prev="Previous", nextPage="Next page",
               tools="Tools", excelRatio="Excel Ratio Converter",
               excelDesc="Split Excel amounts or expenses by ratio, total matching names by column, and copy the results back to Excel. Runs in your browser.",
               readSource="Read the original", introLabel="Editor’s note", about="About", topics="Topics", ring="LONG READS WORTH FINISHING · 值得读完的长文 · ",
               siteDesc="chuhainews curates long reads from X worth finishing — original text with one-tap translation, "
                        "organised by the problem you want to solve."),
}

HOT_QUERIES = {"zh": ["Agent", "定价", "出海", "写作", "Eval", "本地化"],
               "en": ["Agent", "Pricing", "Global", "Writing", "Eval", "Localization"]}


def count(n, ui):
    return f"{n} 篇" if ui == "zh" else f"{n} piece" + ("" if n == 1 else "s")


def fmt(n):
    return (f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "K") if n >= 1000 else str(n)
