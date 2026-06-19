import { useEffect } from 'react'
import ActionNotesBubble from './ActionNotesBubble'
import { useActionNotesContext } from './ActionNotesContext'

type Props = {
  year: number
  month: number
  entity: string
  sessionTitle: string
  route: string
  tab?: string
}

function FloatingStack({ year, month, entity, sessionTitle, route, tab }: Props) {
  const ctx = useActionNotesContext()
  const setFilters = ctx.setFilters
  useEffect(() => {
    setFilters({
      route,
      year,
      month,
      entity,
      tab,
    })
  }, [setFilters, route, year, month, entity, tab])

  return (
    <div className="fixed bottom-6 right-6 z-[59] flex flex-col items-end gap-3">
      <ActionNotesBubble sessionTitle={sessionTitle} route={route} />
    </div>
  )
}

export default function FloatingAssistants(props: Props) {
  return <FloatingStack {...props} />
}
