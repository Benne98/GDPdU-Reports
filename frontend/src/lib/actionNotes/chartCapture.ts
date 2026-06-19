import html2canvas from 'html2canvas'
import type { ViewPinSnapshot } from '../api'

export async function captureChartByTarget(chartId: string): Promise<ViewPinSnapshot['chart'] | null> {
  const el = document.querySelector<HTMLElement>(`[data-expert-chart-target="${chartId}"]`) ?? document.getElementById(chartId)
  if (!el) return null
  try {
    const canvas = await html2canvas(el, {
      backgroundColor: '#ffffff',
      scale: Math.min(2, window.devicePixelRatio || 1.5),
      useCORS: true,
      logging: false,
    })
    return {
      chart_id: chartId,
      title: el.getAttribute('data-chart-title') ?? undefined,
      anchor: chartId,
      image_data_url: canvas.toDataURL('image/png'),
    }
  } catch {
    return {
      chart_id: chartId,
      title: el.getAttribute('data-chart-title') ?? undefined,
      anchor: chartId,
    }
  }
}

