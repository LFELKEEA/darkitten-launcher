"""游戏助手数据与解析的单元测试（不联网）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import game_assistant as ga  # noqa: E402
from darkitten import game_ids  # noqa: E402


class SearchDataTests(unittest.TestCase):
    def test_item_search_by_chinese_name(self):
        values = [item.value for item in game_ids.search("item", "钻石")]
        self.assertIn("minecraft:diamond_sword", values)
        self.assertIn("minecraft:diamond", values)

    def test_category_filter(self):
        tools = game_ids.search("item", "", "工具")
        self.assertTrue(tools)
        self.assertTrue(all(item.category == "工具" for item in tools))

    def test_enchantment_search(self):
        values = [item.value for item in game_ids.search("enchantment", "锋利")]
        self.assertIn("minecraft:sharpness", values)

    def test_all_templates_use_known_widget_kinds(self):
        kinds = {"text", "int", "choice", "picker", "component"}
        for template in ga.COMMAND_TEMPLATES:
            for field in template.fields:
                self.assertIn(field.kind, kinds, f"{template.id}.{field.key}")
                if field.kind == "choice":
                    self.assertTrue(field.options, f"{template.id}.{field.key}")
                    self.assertTrue(
                        all(isinstance(option, game_ids.Option) for option in field.options)
                    )
                if field.kind == "picker":
                    self.assertIn(field.picker, game_ids.DATASETS)

    def test_target_field_is_choice_with_descriptions(self):
        template = ga.TEMPLATE_BY_ID["give"]
        target = template.fields[0]
        self.assertEqual(target.kind, "choice")
        self.assertEqual(target.default, "@s")
        self.assertTrue(any(option.value == "@a" for option in target.options))


class MCModParserTests(unittest.TestCase):
    PAGE = """
    <html><body>
    <div class="result-item">
      <div class="head">
        <div class="class-category"><a href="/class/1-1-1.html"><i></i></a></div>
        <a href="//www.mcmod.cn/class/123.html">机械动力 Create</a>
      </div>
      <div class="body">这是一个<span>大型</span>机械模组。<br>支持 1.21。</div>
      <div class="foot">
        <span>快照时间：</span><span class="value">2026-01-01</span>
        <span>地址：</span><span class="value">mcmod.cn</span>
      </div>
    </div>
    <div class="result-item">
      <div class="head"><a href="/post/456.html">[h1=教程] 新手入门</a></div>
      <div class="body">教程正文。</div>
    </div>
    <div class="search-result-pages"></div>
    <p class="info">找到约 8,964 条结果，共约 299 页</p>
    </body></html>
    """

    def test_parser_skips_empty_category_anchor(self):
        page = ga._parse_mcmod_page(self.PAGE + "&page=3", "机械", 1)
        self.assertEqual(len(page.results), 2)
        first = page.results[0]
        self.assertEqual(first.title, "机械动力 Create")
        self.assertEqual(first.url, "https://www.mcmod.cn/class/123.html")
        self.assertEqual(first.category, "模组")
        self.assertIn("机械模组", first.summary)
        self.assertIn("快照时间", first.meta)

    def test_parser_category_and_total(self):
        page = ga._parse_mcmod_page(self.PAGE + "&page=3", "机械", 1)
        self.assertEqual(page.results[1].category, "教程")
        self.assertEqual(page.results[1].title, "【教程】 新手入门")
        self.assertEqual(page.total_pages, 299)
        self.assertIn("8964", page.total_text.replace(",", ""))
        self.assertEqual(page.page, 3)

    def test_search_url_contains_filter_and_page(self):
        url = ga.mcmod_search_url("机械 动力", 2, 5)
        self.assertIn("key=%E6%9C%BA%E6%A2%B0%20%E5%8A%A8%E5%8A%9B", url)
        self.assertIn("filter=2", url)
        self.assertIn("page=5", url)


if __name__ == "__main__":
    unittest.main()