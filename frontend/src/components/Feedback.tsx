import { ApiError, BackendDownError } from '../api'

/** Turns a thrown error into a message the user can act on. */
export function errorMessage(error: unknown): string {
  if (error instanceof BackendDownError) {
    return 'Backend not running. Start it with: uvicorn backend.api.main:app --port 8000'
  }
  if (error instanceof ApiError) {
    if (error.status === 404) return `No match: ${error.message}`
    return error.message
  }
  return error instanceof Error ? error.message : String(error)
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  const down = error instanceof BackendDownError
  return (
    <div className={down ? 'notice notice-down' : 'notice notice-error'}>
      <strong>{down ? 'Backend unreachable' : 'Request failed'}</strong>
      <span>{errorMessage(error)}</span>
    </div>
  )
}

export function EmptySmilesNotice() {
  return (
    <div className="notice notice-info">
      Draw a structure or paste a SMILES first.
    </div>
  )
}
