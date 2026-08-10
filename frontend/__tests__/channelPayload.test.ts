import type { ChannelForm } from '../src/views/settings/sections/channelPayload'
import { describe, expect, it } from 'vitest'
import { buildChannelPayload } from '../src/views/settings/sections/channelPayload'

const base: ChannelForm = {
  type: 'telegram', display_name: 'Bot', bot_token: '',
  pairing_chat_id: '', pairing_user_id: '', enabled: true, default_qa_type: 'SUPER_AGENT_QA',
  default_session_id: '', session_strategy: 'persistent', delivery_preference: 'reply',
}

describe('channel payload', () => {
  it('keeps an existing Telegram token when the secret field is blank', () => {
    expect(buildChannelPayload(base).bot_token_action).toBe('keep')
  })

  it('sends only the current user pairing identifiers for Feishu', () => {
    const payload = buildChannelPayload({
      ...base, type: 'feishu',
      pairing_user_id: ' ou_1 ', pairing_chat_id: ' oc_1 ',
    })
    expect(payload).toMatchObject({
      type: 'feishu',
      pairing_user_id: 'ou_1', pairing_chat_id: 'oc_1',
    })
    expect(payload).not.toHaveProperty('app_id')
    expect(payload).not.toHaveProperty('app_secret')
  })
})
