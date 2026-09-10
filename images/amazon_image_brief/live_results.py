"""Apply immutable worker snapshots one module at a time on the Tk thread."""
from copy import deepcopy


COPY_FIELDS = ('copy', 'requested_languages', 'chinese_translations', 'design_brief', 'image_prompt', 'compliance_note', 'creative_plan',
               'keywords', 'custom_prompt', 'generation_status', 'generation_error', 'effective_copy_prompt', 'context_fingerprint')
IMAGE_FIELDS = ('reference_image', 'ai_effect_image', 'german_composite_image', 'image_language', 'image_version',
                'image_history', 'image_similarity', 'ocr_text', 'overflow_result',
                'image_qa_result', 'image_generation_status', 'image_generation_error', 'effective_image_prompt', 'style_reference_paths', 'context_fingerprint')


class LiveResults:
    def _begin_live_results(self, operation):
        self._live_operation = operation
        self._export_needs_sync = False
        self._live_copy_done = set()
        self._live_image_done = set()
        self._live_image_revealed = False
        self._pending_copy_ids = set()
        if operation in ('draft', 'regen_all'):
            self._pending_copy_ids = set(self.module_order)
        elif operation == 'regen_copy' and self.current_review_code:
            self._pending_copy_ids.add(self.current_review_code)
        elif operation == 'regen_aplus_copy':
            self._pending_copy_ids = {b.instance_id for b in self.current_briefs if b.channel == '高级A+'}
        if operation == 'draft':
            valid = set(self.module_order)
            self.current_briefs = [item for item in self.current_briefs if self._brief_key(item) in valid]
            for tree in (self.review_tree, self.image_review_tree):
                for iid in tree.get_children():
                    if iid not in valid:
                        tree.delete(iid)
        if self.current_review_code:
            self._load_review_editor()

    def _live_brief(self, snapshot):
        key = self._brief_key(snapshot)
        existing = self._brief_by_code(key)
        if existing is None:
            existing = deepcopy(snapshot)
            self.current_briefs.append(existing)
            order = {iid: index for index, iid in enumerate(self.module_order)}
            self.current_briefs.sort(key=lambda brief: order.get(self._brief_key(brief), len(order)))
        return existing

    def _update_live_rows(self, brief):
        key = self._brief_key(brief)
        copy_values = (brief.channel, brief.module_name, f'V{brief.copy_version}', brief.review_status,
                       brief.generation_status)
        image_values = (brief.sequence, brief.module_name, f'V{brief.image_version}', brief.image_review_status,
                        brief.image_generation_status)
        for tree, values in ((self.review_tree, copy_values), (self.image_review_tree, image_values)):
            if tree.exists(key):
                tree.item(key, values=values)
            else:
                tree.insert('', 'end', iid=key, values=values)
            tree.move(key, '', self.current_briefs.index(brief))

    def _receive_copy_progress(self, event):
        self.creative_workspace.update_progress(event)
        self.status_var.set(f"文案 {event['done']}/{event['total']}｜{event['status']}")
        self.progress_var.set(f"文案 {event['done']}/{event['total']}：{event['module_name']} — {event['focus']}")
        self.live_copy_status_var.set(f"文案进度 {event['done']}/{event['total']}：{event['module_name']}｜{event['status']}")
        incoming = event.get('brief')
        if incoming is None:
            return
        if incoming.creative_plan:
            self.creative_plans[incoming.instance_id] = deepcopy(incoming.creative_plan)
        key = self._brief_key(incoming)
        if key not in self.module_order:
            return
        self._commit_review_edits()
        brief = self._live_brief(incoming)
        finished = event.get('finished', event['status'] not in {'正在生成', '文案校验修正中'})
        if not finished:
            self._pending_copy_ids.add(key)
            brief.generation_status = incoming.generation_status
            brief.generation_error = incoming.generation_error
        elif key not in self._live_copy_done:
            for field in COPY_FIELDS:
                setattr(brief, field, deepcopy(getattr(incoming, field)))
            if not brief.generation_error:
                brief.review_status = '待审核'
                reasons = {'regen_all': '全局AI重生成', 'regen_copy': '局部AI重生成'}
                self._record_version(brief, reasons.get(self._live_operation, '逐模块生成草稿'))
            self._pending_copy_ids.discard(key)
            self._live_copy_done.add(key)
        self._update_live_rows(brief)
        if not self.current_review_code or (finished and self.follow_copy_var.get()):
            self.review_tree.selection_set(key)
            self.review_tree.see(key)
            self._on_review_select()
        if self.current_review_code == key:
            self._load_review_editor()

    def _receive_image_progress(self, event):
        incoming = event['brief']
        key = self._brief_key(incoming)
        if key not in self.module_order:
            return
        self._commit_image_review_edits()
        brief = self._live_brief(incoming)
        new_image = bool(incoming.ai_effect_image and incoming.ai_effect_image != brief.ai_effect_image)
        for field in IMAGE_FIELDS:
            setattr(brief, field, deepcopy(getattr(incoming, field)))
        if new_image:
            brief.image_review_status = incoming.image_review_status
        if event['stage'] in ('complete', 'failed'):
            self._live_image_done.add(key)
        self._update_live_rows(brief)
        self.last_output_dir = event['output_dir']
        self.open_output_button.configure(state='normal')
        self.live_image_status_var.set(f"图片已处理 {len(self._live_image_done)} 张｜{incoming.module_name}｜{event['status']}" +
                                       (f"｜{event['error']}" if event.get('error') else ''))
        ready = event['stage'] != 'running'
        if not self.current_image_review_code or (ready and self.follow_image_var.get()):
            self.image_review_tree.selection_set(key)
            self.image_review_tree.see(key)
            self._on_image_review_select()
        if self.current_image_review_code == key:
            if new_image:
                self.image_review_status_var.set(brief.image_review_status)
            self._display_image_preview()
        if (event['stage'] == 'ai_ready' and self.follow_image_var.get()
                and not getattr(self, '_live_image_revealed', False)):
            self._live_image_revealed = True
            self.tabs.select(self.pages['image'])
            self.export_workspace_tabs.select(0)
            self.export_scroll_frame.scroll_canvas.yview_moveto(0)

    def _finish_copy_results(self, results):
        self._commit_review_edits()
        total = len(results)
        for index, brief in enumerate(results, 1):
            if self._brief_key(brief) not in self._live_copy_done:
                self._receive_copy_progress({'instance_id': brief.instance_id, 'module_name': brief.module_name,
                                             'focus': brief.creative_plan.get('focus', ''), 'status': brief.generation_status,
                                             'error': brief.generation_error, 'done': index, 'total': total,
                                             'finished': True, 'brief': brief})
        self._pending_copy_ids.clear()
        if self.current_review_code:
            self._load_review_editor()

    def _merge_export_results(self, results):
        """Final packaging may add QA; never replace already previewed/edited copy."""
        self._commit_review_edits()
        self._commit_image_review_edits()
        needs_sync = False
        for incoming in results:
            brief = self._live_brief(incoming)
            needs_sync = needs_sync or any(getattr(brief, field) != getattr(incoming, field) for field in
                                          ('copy', 'copy_version', 'review_status', 'review_notes',
                                           'image_review_status', 'image_revision_notes'))
            if self._brief_key(brief) not in self._live_image_done:
                for field in IMAGE_FIELDS:
                    setattr(brief, field, deepcopy(getattr(incoming, field)))
            elif incoming.creative_plan.get('poster'):
                # Final A+ artboard may re-typeset a streamed AI image. Preserve user
                # review edits, but use the actual final exported composite path.
                brief.german_composite_image = incoming.german_composite_image
            for field in ('back_translation_result', 'rule_preflight_result'):
                setattr(brief, field, deepcopy(getattr(incoming, field)))
            self._update_live_rows(brief)
        if not self.current_image_review_code and self.current_briefs:
            self.image_review_tree.selection_set(self._brief_key(self.current_briefs[0]))
            self._on_image_review_select()
        self._display_image_preview()
        return needs_sync

    def _end_live_results(self, interrupted=False):
        if interrupted:
            for brief in self.current_briefs:
                if self._brief_key(brief) in self._pending_copy_ids:
                    brief.generation_status = '未完成 / 本次生成已中断'
                if brief.image_generation_status.startswith('正在') or brief.image_generation_status in ('AI图就绪 / 正在排版质检', '结果图已就绪 / 质检中'):
                    brief.image_generation_status = '未完成 / 已有图片保留'
                self._update_live_rows(brief)
        self._pending_copy_ids.clear()
        if self.current_review_code:
            self._load_review_editor()
        # Completed modules are recoverable even if a later API call failed.
        if hasattr(self, 'profiles'):
            self.profiles.flush(force=True)

    def _receive_api_event(self, payload):
        self.api_inspector.add_event(payload)
        self.export_api_inspector.add_event(payload)
        if payload.get('phase') == 'retry_wait':
            message = f"{payload.get('provider', '供应商')}暂时不可用：等待约{payload.get('wait_seconds', 0):.0f}秒再请求，可暂停/继续。"
            self.status_var.set(message)
            if payload.get('work_type') == '产品上下文分析':
                self.context_status_var.set(message)
