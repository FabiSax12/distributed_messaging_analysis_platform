// Dashboard mirror of shared/routing.py, shared/analysis.py and shared/states.py.

export const CASE_STATUS = {
  queued: { label: 'Queued', tone: 'neutral' },
  processing: { label: 'In progress', tone: 'info' },
  retrying: { label: 'Retrying', tone: 'warn' },
  completed: { label: 'Completed', tone: 'ok' },
  partially_completed: { label: 'Partially completed', tone: 'warn' },
  failed: { label: 'Failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
}

export const TERMINAL_CASE = new Set(['completed', 'partially_completed', 'failed', 'cancelled'])

export const SUBTASK_STATUS = {
  pending: { label: 'Pending', tone: 'neutral' },
  assigned: { label: 'Assigned', tone: 'info' },
  running: { label: 'Running', tone: 'info' },
  retrying: { label: 'Retrying', tone: 'warn' },
  completed: { label: 'Completed', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
}

// Order in which the segments of a case progress bar are stacked.
export const SUBTASK_ORDER = ['completed', 'failed', 'cancelled', 'running', 'assigned', 'retrying', 'pending']

export const OPERATIONS = {
  text_analyze: 'Text analysis',
  image_analyze: 'Image analysis',
  audio_analyze: 'Audio analysis',
}

export const MEDIA_TYPES = { text: 'Text', image: 'Image', audio: 'Audio', video: 'Video' }

// Video has no operation of its own: the audio worker analyzes its audio track.
export const ALLOWED_OPERATIONS = {
  text: ['text_analyze'],
  image: ['image_analyze'],
  audio: ['audio_analyze'],
  video: ['audio_analyze'],
}

export const DEFAULT_OPERATIONS = ALLOWED_OPERATIONS

const EXTENSIONS = {
  txt: 'text', json: 'text',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image', gif: 'image', bmp: 'image',
  mp3: 'audio', wav: 'audio', ogg: 'audio', opus: 'audio', m4a: 'audio', aac: 'audio', flac: 'audio',
  mp4: 'video', mkv: 'video', mov: 'video', webm: 'video', avi: 'video',
}

export function detectMediaType(filename) {
  const ext = filename.split('.').pop()?.toLowerCase()
  return EXTENSIONS[ext] ?? null
}

// CasePriority; the coordinator maps it to AMQP message priority 1..4.
export const PRIORITIES = {
  low: { label: 'Low', tone: 'muted' },
  medium: { label: 'Medium', tone: 'neutral' },
  high: { label: 'High', tone: 'accent' },
  critical: { label: 'Critical', tone: 'bad' },
}

export const DEFAULT_PRIORITY = 'medium'

export const POOLS = {
  text: { label: 'Text', color: 'var(--c-meta)' },
  image: { label: 'Image', color: 'var(--c-video)' },
  audio: { label: 'Audio', color: 'var(--c-audio)' },
}

// RabbitMQ queues: one work queue per pool (priorities live inside it).
export const workQueue = (pool) => `work.${pool}`
export const RESULTS_QUEUE = 'results'
export const DEAD_LETTER_QUEUE = 'dead-letter'

export const ERROR_CODES = {
  unsupported_format: 'Unsupported format',
  corrupt_input: 'Corrupt file',
  processing_error: 'Processing error',
  external_api_error: 'External API error',
  timeout: 'Timed out',
  retries_exhausted: 'Retries exhausted',
  worker_lost: 'Worker lost',
  dead_lettered: 'Dead-lettered message',
  cancelled: 'Cancelled',
  internal: 'Internal error',
}
