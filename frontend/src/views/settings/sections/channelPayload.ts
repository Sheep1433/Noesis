export type ChannelForm = {
  type: 'telegram' | 'feishu'
  display_name: string
  bot_token: string
  pairing_chat_id: string
  pairing_user_id: string
  enabled: boolean
}

export function buildChannelPayload(form: ChannelForm) {
  return {
    ...form,
    pairing_chat_id: form.pairing_chat_id.trim() || undefined,
    pairing_user_id: form.pairing_user_id.trim() || undefined,
    bot_token: form.bot_token.trim() || undefined,
    bot_token_action: form.bot_token.trim() ? 'replace' : 'keep',
  }
}
