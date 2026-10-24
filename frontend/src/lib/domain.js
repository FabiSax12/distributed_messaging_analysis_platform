// Dashboard mirror of shared/routing.py y shared/states.py.

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
  video_convert: 'Video conversion',
  audio_extract: 'Audio extraction',
  video_thumbnail: 'Video thumbnail',
  audio_convert: 'Audio transcoding',
  image_thumbnail: 'Image thumbnail',
  metadata: 'Metadata',
  lyrics: 'Lyrics',
  classify: 'Classification',
}

export const MEDIA_TYPES = { video: 'Video', audio: 'Audio', image: 'Image' }

export const ALLOWED_OPERATIONS = {
  video: ['video_convert', 'audio_extract', 'video_thumbnail', 'metadata', 'classify'],
  audio: ['audio_convert', 'metadata', 'lyrics', 'classify'],
  image: ['image_thumbnail', 'metadata', 'classify'],
}

export const DEFAULT_OPERATIONS = {
  video: ['video_convert', 'audio_extract', 'video_thumbnail'],
  audio: ['audio_convert', 'metadata'],
  image: ['image_thumbnail'],
}

const EXTENSIONS = {
  mp4: 'video', mkv: 'video', mov: 'video', avi: 'video', webm: 'video',
  mp3: 'audio', wav: 'audio', flac: 'audio', ogg: 'audio', m4a: 'audio', aac: 'audio',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image',
}

export function detectMediaType(filename) {
  const ext = filename.split('.').pop()?.toLowerCase()
  return EXTENSIONS[ext] ?? null
}

export const POOLS = {
  video: { label: 'Video', instance: 'c7i-flex.large' },
  audio: { label: 'Audio', instance: 'm7i-flex.large' },
  metadatos: { label: 'Metadata', instance: 't3.micro' },
}

// Consumption order of each pool (POLL_ORDER en shared/routing.py).
export const POLL_ORDER = {
  video: ['video-alta', 'video-normal', 'audio-normal'],
  audio: ['audio-alta', 'audio-normal', 'metadatos-alta', 'metadatos-normal', 'video-normal'],
  metadatos: ['metadatos-alta', 'metadatos-normal', 'audio-normal'],
}

export const ERROR_CODES = {
  unsupported_format: 'Unsupported format',
  corrupt_input: 'Corrupt file',
  processing_error: 'Processing error',
  external_api_error: 'External API error',
  timeout: 'Timed out',
  retries_exhausted: 'Retries exhausted (DLQ)',
  cancelled: 'Cancelled',
  internal: 'Internal error',
}
