const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

/** Backend is unreachable (not running, wrong port, CORS refused). */
export class BackendDownError extends Error {
  constructor() {
    super(`Cannot reach the backend at ${BASE}`)
    this.name = 'BackendDownError'
  }
}

/** Backend answered, but rejected the request (400 invalid SMILES, 404 no match). */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as a list of {loc, msg, ...}
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string }
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined
        return field ? `${field}: ${item.msg}` : item.msg
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal })
  } catch (err) {
    // fetch rejects with TypeError for network failure and AbortError on timeout;
    // neither means the backend said no, so both read as "not reachable".
    throw new BackendDownError()
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      response.status,
      detailToMessage(detail, `${response.status} ${response.statusText}`),
    )
  }
  return response.json() as Promise<T>
}

function post<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    timeoutMs,
  )
}

// --- molecules -------------------------------------------------------------

export type Molecule = {
  id: number
  canonical_smiles: string
  original_smiles: string
  inchikey: string
  source: string
  is_mineral_salt: boolean
  molecular_weight: number
  created_at: string
  image_png_base64?: string | null
}

export type Representation = {
  input_smiles: string
  canonical_smiles: string
  smiles_as_given_canonical: string
  inchikey: string
  molecular_weight: number
  morgan_fingerprint_on_bits: number[]
  morgan_radius: number
  morgan_n_bits: number
  image_png_base64: string
}

export const represent = (smiles: string) =>
  post<Representation>('/molecules/represent', { smiles })

export const getMolecule = (id: number) => request<Molecule>(`/molecules/${id}`)

export type MoleculeStats = { total: number; mineral_salts: number; sources: number }

export const moleculeStats = () => request<MoleculeStats>('/molecules/stats')

export type Resolution = {
  query: string
  canonical_smiles: string
  /** "smiles" when the input already parsed; "pubchem" when a name was looked up. */
  source: 'smiles' | 'pubchem'
  matched_name: string | null
}

/** Accepts a SMILES or a compound name. Names are the common case for people
 *  who are not thinking in SMILES, and used to fail as an unparseable string. */
export const resolveQuery = (query: string) =>
  post<Resolution>('/molecules/resolve', { query }, 20_000)

// --- search ----------------------------------------------------------------

export type SimilarityHit = Molecule & { tanimoto: number }

export const exactSearch = (smiles: string) =>
  post<Molecule>('/search/exact', { smiles })

export const similaritySearch = (
  smiles: string,
  top_n: number,
  min_similarity: number,
) =>
  post<{ query_canonical_smiles: string; count: number; results: SimilarityHit[] }>(
    '/search/similarity',
    { smiles, top_n, min_similarity },
  )

export const substructureSearch = (smiles_pattern: string, top_n: number) =>
  post<{ query_pattern: string; count: number; results: Molecule[] }>(
    '/search/substructure',
    { smiles_pattern, top_n },
  )

// --- retrosynthesis --------------------------------------------------------

export type Reaction = {
  reactants: RouteNode[]
  template_used: number | null
  template_smarts: string | null
  score: number | null
  reaction_smiles: string
  classification: string | null
}

export type RouteNode = {
  molecule_smiles: string
  is_stock_available: boolean
  reactions: Reaction[]
}

export type Route = {
  route_id: number
  state_score: number | null
  scores: Record<string, number>
  number_of_reactions: number
  tree: RouteNode
  image_png_base64: string | null
}

export type Plan = {
  target_smiles: string
  is_solved: boolean
  search_time_seconds: number
  iterations_used: number
  iteration_limit: number
  solved_routes_found: number
  routes_returned: number
  routes: Route[]
}

/** A 500-iteration search takes ~95s server-side, so allow well beyond that. */
export const planRoutes = (
  smiles: string,
  top_n: number,
  iteration_limit: number,
) =>
  post<Plan>(
    '/retrosynthesis/plan',
    { smiles, top_n, iteration_limit, include_images: true },
    300_000,
  )

export type Health = {
  status: string
  model_loaded: boolean
  load_time_seconds: number
  max_iteration_limit: number
}

export const retroHealth = () => request<Health>('/retrosynthesis/health', undefined, 5_000)

// --- QSAR property prediction ----------------------------------------------

export type CoverageRow = {
  alpha: number
  nominal: number
  /** Measured on held-out data, not the nominal label. */
  empirical: number
  /** Wilson 95% CI: coverage is itself an estimate from n_test molecules. */
  empirical_ci_95: number[]
  n_test: number
  mean_width: number
}

export type PropertyModel = {
  model: string
  description: string
  test_rmse: number
  test_mae: number
  test_r2: number
  coverage: CoverageRow[]
}

export type PropertySpec = {
  property: string
  units: string
  description: string
  dataset: string
  default_model: string
  task_type: string
  calibrated_alphas: number[]
  models: PropertyModel[]
}

export type PredictionInterval = {
  lower: number
  upper: number
  alpha: number
  nominal_coverage: number
  method: string
  empirical_coverage: number
  empirical_coverage_ci_95: number[]
  n_calibration: number
  n_test: number
  note: string
}

export type Prediction = {
  property: string
  predicted_value: number
  units: string
  model_used: string
  model_performance: {
    test_rmse: number
    test_mae: number
    test_r2: number
    split: string
    note: string
  }
  prediction_interval: PredictionInterval
  applicability: {
    max_train_similarity: number
    structurally_familiar: boolean
    threshold: number
    nearest_training_smiles: string[]
    note: string
  }
}

export const listProperties = () =>
  request<{ properties: PropertySpec[] }>('/predict/properties')

export const predictProperty = (
  smiles: string,
  property: string,
  model: string | undefined,
  alpha: number,
) => post<Prediction>('/predict/property', { smiles, property, model, alpha })
