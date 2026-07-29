export type ChannelForm = {
  type: 'telegram' | 'feishu'
  display_name: string
  bot_token: string
  pairing_chat_id: string
  pairing_user_id: string
  enabled: boolean
  default_qa_type: string
  default_session_id: string
  session_strategy: 'persistent' | 'new_per_message'
  delivery_preference: 'reply' | 'silent'
}

export function buildChannelPayload(form: ChannelForm) {
  return {
    ...form,
    pairing_chat_id: form.pairing_chat_id.trim() || undefined,
    pairing_user_id: form.pairing_user_id.trim() || undefined,
    default_session_id: form.default_session_id.trim() || undefined,
    bot_token: form.bot_token.trim() || undefined,
    bot_token_action: form.bot_token.trim() ? 'replace' : 'keep',
  }
}
