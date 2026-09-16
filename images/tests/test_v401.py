"""Regression tests for module selection and the persistent split workspace."""
from copy import deepcopy
from unittest.mock import patch
import unittest

import test_v40
from test_v40 import sample
from amazon_image_brief.models import ModuleInstance


class DirectNavigationTests(unittest.TestCase):
    setUp = test_v40.DirectDesktopTests.setUp
    tearDown = test_v40.DirectDesktopTests.tearDown

    def display(self, many=False):
        if many:
            project = sample(self.folder)
            recipe = project.module_recipes['m1']
            project.module_instances = [ModuleInstance(f'm{i}', 'MAIN_DETAIL', '主图') for i in range(1, 41)]
            project.module_recipes = {f'm{i}': dict(deepcopy(recipe), direct_prompt=f'产品 P01，第 {i} 个模块独立提示词') for i in range(1, 41)}
            self.app.populate_project(project)
        self.root.attributes('-alpha', 0)
        self.root.geometry('1120x720')
        self.root.deiconify()
        self.app.tabs.select(self.app.direct_page)
        self.app.direct_tabs.select(self.app.direct_inputs_tab)
        self.root.update()

    def click_module(self, iid):
        tree = self.app.direct_module_tree
        tree.see(iid)
        self.root.update()
        x, y, w, h = tree.bbox(iid)
        tree.event_generate('<ButtonPress-1>', x=x+75, y=y+h//2)
        tree.event_generate('<ButtonRelease-1>', x=x+75, y=y+h//2)
        self.root.update()

    def test_status_refresh_preserves_rows_focus_and_scroll(self):
        self.display(many=True)
        tree = self.app.direct_module_tree
        self.click_module('m25')
        before = tree.yview()
        self.app._set_text(self.app.direct_prompt_text, '我修改过的 P01 提示词', editable=True)
        with patch.object(tree, 'delete', wraps=tree.delete) as deleted:
            self.app._refresh_direct_rows()
            self.root.update()
            deleted.assert_not_called()
        self.assertEqual(('m25',), tree.selection())
        self.assertEqual('m25', tree.focus())
        self.assertEqual(before, tree.yview())
        self.assertEqual('我修改过的 P01 提示词', self.app.direct_prompt_text.get('1.0', 'end-1c'))

    def test_manual_selection_prevents_completed_result_from_stealing_focus(self):
        self.display()
        app = self.app
        app.busy = True
        app.follow_image_var.set(True)
        self.click_module('m2')
        app._receive_direct_image(dict(id='m1', language='de', done=1, total=3, output_dir=str(self.folder),
                                      result=dict(status='已完成', final_image=str(self.folder/'product.png'), version=1)))
        self.root.update()
        self.assertEqual('m2', app.direct_current_id)
        self.assertFalse(app.follow_image_var.get())
        self.assertEqual(str(app.direct_inputs_tab), app.direct_tabs.select())
        self.assertEqual('已完成', app.module_recipes['m1']['direct_results']['de']['status'])
        app.busy = False

    def test_real_clicks_and_keyboard_switch_commit_only_the_previous_module(self):
        self.display(many=True)
        app = self.app
        for index in (25, 3, 28, 4):
            self.click_module(f'm{index}')
            self.assertEqual(f'm{index}', app.direct_current_id)
            self.assertEqual(f'产品 P01，第 {index} 个模块独立提示词', app.direct_prompt_text.get('1.0', 'end-1c'))
            app.direct_prompt_text.insert('end', ' · 已编辑')
            app.profiles.flush(force=True)
            app._refresh_direct_rows()
            self.root.update()
        self.click_module('m25')
        self.assertEqual('产品 P01，第 25 个模块独立提示词 · 已编辑', app.direct_prompt_text.get('1.0', 'end-1c'))
        app.direct_module_tree.focus_force()
        app.direct_module_tree.event_generate('<KeyPress-Down>')
        self.root.update()
        self.assertEqual('m26', app.direct_current_id)
        self.assertEqual('产品 P01，第 26 个模块独立提示词', app.direct_prompt_text.get('1.0', 'end-1c'))

    def test_fixed_list_and_prompt_remain_visible_while_galleries_scroll(self):
        self.display(many=True)
        app = self.app
        tree, prompt = app.direct_module_tree, app.direct_prompt_text
        self.assertGreaterEqual(tree.winfo_height(), 270)
        self.assertLess(tree.winfo_rootx()+tree.winfo_width(), prompt.winfo_rootx())
        initial = (tree.winfo_rooty(), prompt.winfo_rooty())
        app.direct_inputs_tab.scroll_canvas.yview_moveto(1)
        self.root.update()
        self.assertEqual(initial, (tree.winfo_rooty(), prompt.winfo_rooty()))
        self.assertLess(prompt.winfo_rooty()+prompt.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
        app.direct_split.sashpos(0, 850)
        app._resize_direct_split()
        self.root.update()
        self.assertGreaterEqual(app.direct_tabs.winfo_width(), 610)
        self.assertGreaterEqual(app.direct_inputs_tab.scroll_canvas.winfo_height(), 80)
        gallery = app.direct_galleries['reference']
        self.assertGreater(gallery.tree.winfo_width(), 200)
        self.assertLessEqual(gallery.preview_label.winfo_rootx()+gallery.preview_label.winfo_width(),
                             self.root.winfo_rootx()+self.root.winfo_width())

    def test_no_follow_respected_for_first_result_and_can_be_reenabled(self):
        self.display()
        app = self.app
        self.click_module('m2')
        app.busy = True
        app.follow_image_var.set(False)
        event = dict(id='m1', language='de', done=1, total=2, output_dir=str(self.folder),
                     result=dict(status='已完成', final_image=str(self.folder/'product.png'), version=1))
        app._receive_direct_image(event)
        self.root.update()
        self.assertEqual('m2', app.direct_current_id)
        app.follow_image_var.set(True)
        app._receive_direct_image(event)
        self.root.update()
        self.assertEqual('m1', app.direct_current_id)
        self.assertEqual(str(app.direct_results_tab), app.direct_tabs.select())
        app.busy = False

    def test_selection_reorder_and_removed_module_choose_a_valid_editor(self):
        self.display()
        app = self.app
        self.click_module('m2')
        app.module_instances.reverse()
        app._load_direct_modules()
        self.root.update()
        self.assertEqual(('m2', 'm1'), app.direct_module_tree.get_children())
        self.assertEqual('m2', app.direct_current_id)
        app.module_instances = [i for i in app.module_instances if i.instance_id != 'm2']
        app._load_direct_modules()
        self.root.update()
        self.assertEqual('m1', app.direct_current_id)
        app.module_instances = []
        app._load_direct_modules()
        self.root.update()
        self.assertEqual('', app.direct_current_id)
        self.assertEqual('', app.direct_prompt_text.get('1.0', 'end-1c'))

    def test_review_notes_do_not_leak_across_module_or_language(self):
        self.display()
        app = self.app
        for iid in ('m1', 'm2'):
            app.module_recipes[iid]['direct_results'] = {lang: {'status': '已完成', 'revision_notes': iid+lang} for lang in ('de', 'fr')}
        app._reload_direct_selection()
        self.click_module('m2')
        self.assertEqual('m2de', app.direct_revision_text.get('1.0', 'end-1c'))
        app._set_text(app.direct_revision_text, 'm2de修改', editable=True)
        app.direct_result_tree.selection_set('fr')
        self.root.update()
        self.assertEqual('m2fr', app.direct_revision_text.get('1.0', 'end-1c'))
        app._set_text(app.direct_revision_text, 'm2fr修改', editable=True)
        app.language_vars['it'].set(True)
        app._direct_languages_changed()
        self.root.update()
        self.click_module('m1')
        self.assertEqual('m1fr', app.direct_revision_text.get('1.0', 'end-1c'))
        self.assertEqual('m2de修改', app.module_recipes['m2']['direct_results']['de']['revision_notes'])
        self.assertEqual('m2fr修改', app.module_recipes['m2']['direct_results']['fr']['revision_notes'])
        self.assertFalse(app._direct_loading)

    def test_settings_languages_and_output_share_saved_configuration(self):
        self.display()
        app = self.app
        app._show_direct_settings()
        window = app.direct_settings_window
        window.attributes('-alpha', 0)
        self.root.update()
        from amazon_image_brief.languages import TARGET_LANGUAGES
        self.assertEqual(len(TARGET_LANGUAGES)+6, len(app.direct_language_checks))
        app.language_vars['fr'].set(True)
        app.output_root_var.set(str(self.folder/'changed-output'))
        app.quality_var.set('high')
        app._direct_languages_changed()
        self.assertIn('4 张图', app.direct_count_var.get())
        window.destroy()
        app._direct_editable(False)
        app._direct_editable(True)
        snapshot = app.profiles.capture()
        app.profiles.restore(snapshot)
        self.assertTrue(app.language_vars['fr'].get())
        self.assertEqual('high', app.quality_var.get())
        self.assertEqual(str(self.folder/'changed-output'), app.output_root_var.get())

    def test_all_themes_keep_main_actions_and_both_panes_accessible(self):
        from amazon_image_brief.ui_themes import THEMES
        self.display()
        app = self.app
        for theme in THEMES:
            app.theme_var.set(theme)
            app._apply_theme()
            self.root.update()
            right_edge = self.root.winfo_rootx()+self.root.winfo_width()
            pause, resume, _status, stop = app.control_widgets['image']
            for widget in (*app.direct_input_buttons[:4], pause, resume, stop):
                self.assertTrue(widget.winfo_ismapped(), theme)
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), right_edge, theme)
            self.assertGreaterEqual(app.direct_module_tree.winfo_height(), 270, theme)
            self.assertGreaterEqual(app.direct_inputs_tab.scroll_canvas.winfo_height(), 100, theme)
            app.direct_tabs.select(app.direct_api_inspector)
            self.root.update()
            self.assertGreater(app.direct_api_inspector.request_text.winfo_width(), 400)
            app.direct_tabs.select(app.direct_inputs_tab)

    def test_profile_restores_selected_module_into_visible_list(self):
        self.display(many=True)
        app = self.app
        self.click_module('m31')
        snapshot = app.profiles.capture()
        app.direct_module_tree.yview_moveto(0)
        app.profiles.restore(snapshot)
        self.root.update()
        self.assertEqual('m31', app.direct_current_id)
        self.assertTrue(app.direct_module_tree.bbox('m31'))


if __name__ == '__main__':
    unittest.main()
