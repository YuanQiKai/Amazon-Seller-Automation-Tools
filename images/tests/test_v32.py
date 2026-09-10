from copy import deepcopy
from pathlib import Path
import json
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from PIL import Image

from amazon_image_brief.brief_generator import BriefGenerator
from amazon_image_brief.creative_ai import CreativeAI
from amazon_image_brief.creative_planner import plan_modules, apply_layout, plan_description
from amazon_image_brief.creative_planning_ai import rebuild_plan
from amazon_image_brief.typography import PRESETS, element_layout, validate_element
from amazon_image_brief.layout_editor import LayoutEditor
from amazon_image_brief.models import ProductProject
from amazon_image_brief.ui_themes import THEMES
from amazon_image_brief.service import GenerationService
from amazon_image_brief.ai_client import OpenAIClient
from amazon_image_brief.models import GenerationOptions
from test_v28 import PlannedAI, project_at
import test_v31


class PlanningAI(PlannedAI):
    def __init__(self, invalid=False):
        super().__init__()
        self.planning_requests = []
        self.invalid = invalid

    def generate_json(self, prompt, model):
        if not prompt.startswith('PLAN_CREATIVE_MODULE'):
            return super().generate_json(prompt, model)
        payload = json.loads(prompt[prompt.index('{"instance_id"'):])
        self.planning_requests.append(payload)
        base = payload['base_plan']
        result = dict(focus='双排万向轮 · '+payload['instance_id'], angle='在机场平稳推行的细节角度',
                      evidence=['产品录入：双排万向轮', '功能录入：平稳推行'], visual=payload['user_module_prompt'] or '轮组特写配部位引线',
                      rationale_zh='标题引导后阅读轮组证据', pc=element_layout('hotspots', base['copy_slots']),
                      mobile=element_layout('hotspots', base['copy_slots'], True))
        if self.invalid:
            result.pop('evidence')
        return result


class PlanningTests(unittest.TestCase):
    def project(self):
        project = project_at('unused')
        project.options.ai_plan_before_copy = True
        project.options.optimize_copy_with_ai = True
        project.module_recipes['detail1'] = {'copy_prompt': '强调轮组，镜头靠近轮轴'}
        return project

    def test_global_planning_before_copy_and_real_time_plan_events(self):
        project = self.project()
        client, events = PlanningAI(), []
        briefs = BriefGenerator(client, events.append).generate(project)
        self.assertEqual(2, len(client.planning_requests))
        self.assertEqual(2, len(client.requests))
        self.assertEqual('强调轮组，镜头靠近轮轴', briefs[1].creative_plan['visual'])
        self.assertIn('双排万向轮', client.requests[0])
        self.assertIn('产品录入：双排万向轮', client.requests[0])
        self.assertIn('AI完成', briefs[1].generation_status)
        planned = [e for e in events if e['status'] == '规划完成 / 正在生成文案']
        self.assertEqual(2, len(planned))
        self.assertTrue(all(not e['finished'] for e in planned))
        self.assertTrue(planned[0]['brief'].creative_plan['planning_response'])
        self.assertTrue(client.planning_requests[1]['previous_modules'])

    def test_local_regeneration_updates_all_planning_fields(self):
        project = self.project()
        brief = BriefGenerator()._local_briefs(project)[1]
        client = PlanningAI()
        BriefGenerator(client).regenerate_copy(project, brief, '突出稳定性')
        self.assertEqual('突出稳定性', client.planning_requests[0]['revision'])
        self.assertEqual('在机场平稳推行的细节角度', brief.creative_plan['angle'])
        self.assertTrue(brief.creative_plan['planning_prompt'].startswith('PLAN_CREATIVE_MODULE'))
        self.assertTrue(brief.copy['de'])
        self.assertTrue(brief.creative_plan['evidence'])

    def test_manual_fields_protected_and_explicit_rebuild_replaces_them(self):
        project = self.project()
        base = plan_modules(project)['detail1']
        base.update(focus='手动指定功能', angle='手动角度', manual=True, manual_fields=['focus', 'angle'])
        project.creative_plans['detail1'] = base
        value = rebuild_plan(PlanningAI(), project, 'detail1', base)
        self.assertEqual('手动指定功能', value['focus'])
        self.assertEqual('手动角度', value['angle'])
        value = rebuild_plan(PlanningAI(), project, 'detail1', value, force=True)
        self.assertNotEqual('手动指定功能', value['focus'])
        self.assertEqual([], value['manual_fields'])

    def test_invalid_planning_does_not_call_copy_or_destroy_original(self):
        project = self.project()
        base = plan_modules(project)
        project.copy_overrides['detail1'] = {'de': 'Original approved text'}
        client = PlanningAI(invalid=True)
        briefs = BriefGenerator(client).generate(project)
        self.assertEqual([], client.requests)
        self.assertEqual('Original approved text', briefs[1].copy['de'])
        self.assertEqual(base['detail1']['focus'], briefs[1].creative_plan['focus'])
        self.assertIn('事实依据', briefs[1].generation_error)

    def test_planning_logs_do_not_nest_recursively_on_rebuild(self):
        project, client = self.project(), PlanningAI()
        base = plan_modules(project)['detail1']
        for _ in range(4):
            base = rebuild_plan(client, project, 'detail1', base)
        self.assertNotIn('planning_prompt', client.planning_requests[-1]['base_plan'])
        self.assertLess(len(base['planning_prompt']), 13000)

    def test_unique_preset_geometry_pc_mobile(self):
        slots = ['标题', '副标题', '证据1', '证据2', '证据3', '证据4']
        for mobile in (False, True):
            geometry = []
            for key in PRESETS:
                plan = element_layout(key, slots, mobile)
                for e in plan['elements']:
                    validate_element(e)
                geometry.append(json.dumps([plan['product_box'], plan['elements']], sort_keys=True))
            # Each named preset has distinct effective coordinates/anchors.
            self.assertEqual(len(geometry), len(set(geometry)))

    def test_custom_slots_scenes_and_manual_evidence_survive_roundtrip(self):
        project = self.project()
        base = plan_modules(project)['detail1']
        base['copy_slots'].append('自定义描述')
        apply_layout(base, 'custom')
        base.update(manual=True, angle='新增角度', evidence=['手动核对产品标签'])
        for d in ('pc', 'mobile'):
            base[d]['scenes'] = [dict(label='机场场景', box=[.2, .3, .4, .4], align='left', kind='scene')]
        project.creative_plans['detail1'] = base
        restored = plan_modules(ProductProject.from_dict(project.to_dict()))['detail1']
        self.assertEqual(base['copy_slots'], restored['copy_slots'])
        self.assertEqual('机场场景', restored['mobile']['scenes'][0]['label'])
        self.assertEqual(base['evidence'], restored['evidence'])
        self.assertIn('机场场景', plan_description(restored))
        value = rebuild_plan(PlanningAI(), project, 'detail1', restored, force=True)
        self.assertEqual(restored['pc'], value['pc'])

    def test_explicit_ai_planning_emits_results_without_copy_generation(self):
        project, client, events = self.project(), PlanningAI(), []
        CreativeAI(client, on_item=events.append).run(project, ['white', 'detail1'], 'plan')
        self.assertEqual(1, len(events))
        self.assertEqual('plan', events[0]['action'])
        self.assertIn('planning_response', events[0]['plan'])
        self.assertEqual([], client.requests)

    def test_service_keeps_ai_plan_and_archives_batch_image_versions(self):
        class Client(PlanningAI):
            image_available = True
            def generate_image(self, prompt, destination, **_kwargs):
                self.last_image_prompt = prompt
                destination.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (1000, 1000), '#cad4ef').save(destination, 'JPEG')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project, client = self.project(), Client()
            project.options.output_root = str(root/'outputs')
            project.options.generate_ai_images = True
            product = root/'product.jpg'
            Image.new('RGB', (800, 800), 'white').save(product)
            project.product_image_paths = [str(product)]
            service = GenerationService(root, client, export_excel_on_images=False)
            first = service.run(project)
            briefs = first['briefs']
            old_path = briefs[1].ai_effect_image
            old_bytes = Path(old_path).read_bytes()
            self.assertIn('双排万向轮', briefs[1].creative_plan['focus'])
            self.assertNotIn('PLAN_CREATIVE_MODULE', client.last_image_prompt)
            second = service.run(project, briefs=briefs)
            revised = second['briefs'][1]
            self.assertEqual(2, revised.image_version)
            self.assertEqual(old_path, revised.image_history[-1]['ai_effect_image'])
            self.assertEqual(old_bytes, Path(old_path).read_bytes())
            self.assertNotEqual(old_path, revised.ai_effect_image)

    def test_planning_request_and_response_logged_with_module_context(self):
        project = self.project()
        base = plan_modules(project)['detail1']
        fake = PlanningAI()
        reply = rebuild_plan(fake, project, 'detail1', base)['planning_response']
        class Response:
            status = 200
            headers = {'Content-Type': 'application/json'}
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def read(self): return json.dumps({'choices': [{'message': {'content': json.dumps(reply, ensure_ascii=False)}}]}).encode()
            def getcode(self): return 200
        events = []
        client = OpenAIClient(options=GenerationOptions(text_protocol='chat_completions', cache_enabled=False, max_retries=0, api_debug_enabled=False, requests_per_minute=100000),
                              text_api_key='v32-unit-secret', on_api_event=events.append)
        with patch('urllib.request.urlopen', return_value=Response()):
            result = rebuild_plan(client, project, 'detail1', base)
        requests = [e for e in events if e['phase'] == 'request']
        responses = [e for e in events if e['phase'] == 'response']
        self.assertEqual('detail1', requests[0]['instance_id'])
        self.assertEqual('AI卖点与排版规划', requests[0]['work_type'])
        self.assertIn('强调轮组', json.dumps(requests[0], ensure_ascii=False))
        self.assertTrue(responses)
        self.assertNotIn('v32-unit-secret', json.dumps(events))
        self.assertEqual(reply['focus'], result['focus'])


class DesktopV32Tests(unittest.TestCase):
    setUp = test_v31.DesktopV31Tests.setUp
    tearDown = test_v31.DesktopV31Tests.tearDown

    def select_plan(self, iid='detail1'):
        self.app.populate_project(project_at(self.temp.name))
        self.app.creative_workspace.tree.selection_set(iid)
        self.app.creative_workspace.select()

    def test_merged_navigation_and_local_actions(self):
        app = self.app
        self.assertEqual(['1  分析上下文', '2  卖点与排版审核', '3  多语言文案与版本审核', '4  接口请求与返回 JSON'],
                         [app.copy_workspace_tabs.tab(t, 'text') for t in app.copy_workspace_tabs.tabs()])
        self.assertFalse(app.module_prompt_text.master.winfo_manager())
        self.assertEqual(app.creative_workspace.prompt_host, app.recipe_panels['copy']['frame'].master)
        buttons = [w.cget('text') for w in app.review_language_combo.master.winfo_children() if isinstance(w, ttk.Button)]
        self.assertNotIn('局部重生成图片', buttons)
        self.assertNotIn('蒙版选区式重生', buttons)
        self.assertNotIn('局部重生成文案', buttons)
        self.assertEqual(9, app.self_check_text.cget('height'))

    def test_prompt_follows_planning_selection_and_autosaves_edits(self):
        self.select_plan()
        app = self.app
        panel = app.recipe_panels['copy']
        self.assertEqual('detail1', panel['id'])
        panel['editor'].insert('1.0', '轮组微距')
        app.creative_workspace.angle_var.set('轮轴近景')
        app.creative_workspace.evidence_var.set('录入双排轮；实拍轮轴')
        project = app.collect_project(sync_modules=False)
        self.assertEqual('轮组微距', project.module_recipes['detail1']['copy_prompt'])
        self.assertEqual('轮轴近景', project.creative_plans['detail1']['angle'])
        app.creative_workspace.tree.selection_set('detail2')
        app.creative_workspace.select()
        self.assertEqual('detail2', panel['id'])
        self.assertEqual('', panel['editor'].get('1.0', 'end-1c'))

    def test_all_themes_use_checkmark_and_stable_tab_geometry(self):
        app = self.app
        self.root.attributes('-alpha', 0)
        self.root.geometry('1120x720')
        self.root.deiconify()
        before = None
        for name in THEMES:
            app.theme_var.set(name)
            app._apply_theme()
            style = ttk.Style(self.root)
            self.assertIn(app.theme_manager.images[name][0], str(style.layout('TCheckbutton')))
            for tab in app.tabs.tabs():
                app.tabs.select(tab)
                self.root.update()
                dims = (self.root.winfo_width(), self.root.winfo_height(), app.tabs.winfo_width(), app.tabs.winfo_height())
                before = before or dims
                self.assertEqual(before, dims)
        snapshot = app.profiles.capture()
        self.assertEqual(list(THEMES)[-1], snapshot['ui']['variables']['theme_var'])

    def test_legacy_copy_tab_migration_and_new_theme_restore(self):
        app = self.app
        snapshot = app.profiles.capture()
        snapshot['ui']['variables']['theme_var'] = list(THEMES)[2]
        for old, expected in ((0, 'review'), (1, 'plan'), (2, 'api'), (3, 'context'), (4, 'plan')):
            snapshot['ui'].pop('copy_page_key', None)
            snapshot['ui'].update(ui_version=31, copy_tab=old)
            app.profiles.restore(snapshot)
            self.assertEqual(str(app.copy_pages[expected]), app.copy_workspace_tabs.select())
        self.assertEqual(list(THEMES)[2], app.theme_var.get())

    def test_custom_editor_add_delete_text_and_scenes(self):
        base = plan_modules(project_at(self.temp.name))['detail1']
        editor = LayoutEditor(self.root, base, lambda _p: None)
        editor.withdraw()
        editor.add_text('轮子描述')
        editor.add_scene('机场推行场景')
        self.assertEqual(5, len(editor.plan['copy_slots']))
        self.assertEqual(1, len(editor.plan['mobile']['scenes']))
        self.assertEqual('custom', editor.plan['layout'])
        editor.current = 5
        editor.load_fields()
        editor.remove_region()
        self.assertEqual([], editor.plan['pc']['scenes'])
        editor.current = 4
        editor.load_fields()
        editor.remove_region()
        self.assertEqual(list(range(4)), [i['slot'] for i in editor.plan['pc']['elements']])
        editor.destroy()

    def test_image_comparison_updates_immediately_and_handles_missing_old_file(self):
        self.select_plan()
        app = self.app
        app.current_briefs = BriefGenerator()._local_briefs(app.collect_project())
        brief = app._brief_by_code('detail1')
        a, b = Path(self.temp.name)/'before.jpg', Path(self.temp.name)/'after.jpg'
        Image.new('RGB', (700, 600), 'red').save(a)
        Image.new('RGB', (700, 600), 'blue').save(b)
        brief.ai_effect_image = brief.german_composite_image = str(a)
        app._refresh_image_review_tree('detail1')
        incoming = deepcopy(brief)
        incoming.image_version = 2
        incoming.image_history = [dict(version=1, ai_effect_image=str(a), revision_notes='改为蓝色背景')]
        incoming.ai_effect_image = incoming.german_composite_image = str(b)
        incoming.image_generation_status = '结果图已就绪 / 质检中'
        app._receive_image_progress(dict(brief=incoming, stage='ai_ready', output_dir=self.temp.name, status=incoming.image_generation_status))
        self.assertEqual(str(a), app._image_compare_paths['before'])
        self.assertEqual(str(b), app._image_compare_paths['after'])
        self.assertEqual({'before', 'after'}, set(app._image_compare_photos))
        self.assertIn('改为蓝色', app.image_compare_notes['before'].get())
        self.assertTrue(a.is_file())
        brief.image_history[0]['ai_effect_image'] = str(Path(self.temp.name)/'missing.jpg')
        app._refresh_image_comparison()
        self.assertIn('缺失', app.image_compare_labels['before'].cget('text'))

    def test_local_copy_can_start_without_previous_global_generation(self):
        self.select_plan()
        with patch.object(self.app, 'regenerate_selected_copy') as regenerate:
            self.app.generate_planned_copy()
        regenerate.assert_called_once()
        self.assertEqual('detail1', self.app.current_review_code)
        self.assertIsNotNone(self.app._brief_by_code('detail1'))

    def test_ai_rebuild_one_plan_keeps_all_other_modules_visible(self):
        self.select_plan()
        app = self.app
        before = list(app.creative_workspace.tree.get_children())
        project = app.collect_project()
        base = plan_modules(project)['detail1']
        plan = rebuild_plan(PlanningAI(), project, 'detail1', base)
        app._receive_recipe(dict(id='detail1', action='plan', recipe={'copy_prompt': '指定轮组'}, plan=plan, done=1, total=1))
        self.assertEqual(before, list(app.creative_workspace.tree.get_children()))
        self.assertEqual('detail1', app.creative_workspace.current_id)
        self.assertEqual(plan['focus'], app.creative_workspace.focus_var.get())
        self.assertIn('规划完成', app.creative_workspace.tree.set('detail1', 'status'))


if __name__ == '__main__':
    unittest.main()
