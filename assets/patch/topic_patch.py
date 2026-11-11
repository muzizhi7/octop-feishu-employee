"""Native reply/thread routing. No permission or subscription expansion."""
def apply(source):
    marker='    def configure_controls('
    method='''    def _employee_group_activation(self, chat_type: str, chat_id: str, sender_id: str, thread_id: str, mentions: list[Any], preferences: dict[str, Any]) -> bool:
        if chat_type != 'group' or self._is_bot_mentioned(chat_type, mentions):
            return True
        trigger = preferences['group_trigger']
        if trigger == 'all':return True
        controls = getattr(self, '_controls', None)
        if trigger != 'topic' or not thread_id or controls is None:return False
        # Snapshot the main-loop-owned journal before inspecting on the SDK thread.
        return any(t.chat == chat_id and t.owner == sender_id
                   and t.message.metadata.get('thread_id') == thread_id
                   for t in tuple(controls.turns.values()))

    def get_debounce_key(self, message: InboundMessage) -> str:
        meta = message.metadata or {}
        if meta.get('chat_type') == 'group':
            from octop_gateway.channels.feishu_controls import group_conversation_key
            return group_conversation_key(message)
        return super().get_debounce_key(message)

'''
    assert source.count(marker)==1
    source=source.replace(marker,method+marker,1)
    event='"thread_id": getattr(message, "thread_id", "") or "",'
    if event not in source:
        # Guard the exact existing spelling before changing event metadata.
        event='"thread_id": getattr(message, "thread_id", ""),'
    assert source.count(event)==1
    source=source.replace(event,event+'\n            "root_id": getattr(message, "root_id", "") or "",\n            "parent_id": getattr(message, "parent_id", "") or "",',1)
    marker='        bot_mentioned = self._is_bot_mentioned(chat_type, mentions)\n'
    assert source.count(marker)==1
    source=source.replace(marker,'''        from octop_gateway.channels.feishu_preferences import load_preferences, validate
        snapshot = raw_payload.get('octop_employee_preferences')
        employee_preferences = validate(snapshot) if isinstance(snapshot, dict) else load_preferences(self.channel_id)
        bot_mentioned = self._employee_group_activation(chat_type, chat_id, sender_id, thread_id, mentions, employee_preferences)
''',1)
    receive_marker='            mentions = self._normalize_mentions(getattr(message, "mentions", None))\n'
    assert source.count(receive_marker)==1
    source=source.replace(receive_marker,receive_marker+'''
            from octop_gateway.channels.feishu_preferences import load_preferences
            employee_preferences = load_preferences(self.channel_id)
            if not self._employee_group_activation(message.chat_type, message.chat_id, sender_id,
                    str(getattr(message, 'thread_id', '') or ''), mentions, employee_preferences):
                return  # No typing reaction, media cache, queue or model for passive chatter.
''',1)
    source=source.replace('            raw_payload = {\n','            raw_payload = {\n                "octop_employee_preferences": employee_preferences,\n',1)
    marker='            "bot_mentioned": bot_mentioned,\n'
    source=source.replace(marker,marker+'            "octop_employee_preferences": employee_preferences,\n            "root_id": str(raw_payload.get("root_id") or ""),\n            "parent_id": str(raw_payload.get("parent_id") or ""),\n',1)
    return source
