import html2canvas from 'html2canvas'

export async function captureExpertChartScreenshotByAnchor(anchor?: string): Promise<string | null> {
  if (!anchor) return null
  const target =
    document.querySelector<HTMLElement>(`[data-expert-chart-target="${anchor}"]`) ??
    document.getElementById(anchor)
  if (!target) return null
  try {
    const canvas = await html2canvas(target, {
      backgroundColor: '#ffffff',
      scale: Math.min(2, window.devicePixelRatio || 1.5),
      useCORS: true,
      logging: false,
    })
    return canvas.toDataURL('image/png')
  } catch {
    return null
  }
}

