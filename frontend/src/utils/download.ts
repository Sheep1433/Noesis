export function downloadFile(boldData: BlobPart, filename = 'download', type?: string) {
  const blob = boldData instanceof Blob
    ? boldData
    : new Blob([boldData], { type })

  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.style.display = 'none'
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}
