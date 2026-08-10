export function assertStrictMessageSequence(
  messages: Array<{ message_sequence?: number }>,
): void {
  const invalid = messages.some((message, index) =>
    !Number.isInteger(message.message_sequence)
    || (index > 0 && message.message_sequence! <= messages[index - 1].message_sequence!),
  )
  if (invalid) {
    throw new Error('消息顺序异常，请重新加载')
  }
}
