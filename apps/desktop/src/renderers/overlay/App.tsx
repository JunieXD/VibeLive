import { useEffect, useState } from 'react'
import type { BarrageEvent } from '../../shared/contracts'

type VisibleBarrage = BarrageEvent & {
  lane: number
}

function laneFor(id: string): number {
  return [...id].reduce((total, character) => total + character.charCodeAt(0), 0) % 7
}

export function App(): React.JSX.Element {
  const [items, setItems] = useState<VisibleBarrage[]>([])

  useEffect(() => {
    const removeBarrage = window.advxOverlay.onBarrage((event) => {
      setItems((current) => [...current.slice(-12), { ...event, lane: laneFor(event.barrageId) }])
      window.setTimeout(() => {
        setItems((current) => current.filter((item) => item.barrageId !== event.barrageId))
      }, 9000)
    })
    const clear = window.advxOverlay.onClear(() => setItems([]))

    return () => {
      removeBarrage()
      clear()
    }
  }, [])

  return (
    <main className="overlay-root" aria-label="弹幕覆盖层">
      <div className="ai-watermark">ADVX LIVE · AI AUDIENCE</div>
      {items.map((item) => (
        <div
          className="overlay-barrage"
          key={item.barrageId}
          style={{
            '--lane': item.lane,
            '--barrage-color': item.color
          } as React.CSSProperties}
        >
          <span className="overlay-name">{item.audienceName} · AI</span>
          <span>{item.text}</span>
        </div>
      ))}
    </main>
  )
}
