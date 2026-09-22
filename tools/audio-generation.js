#!/usr/bin/env node

const fs = require('fs')
const path = require('path')
const readline = require('readline')

const DEFAULT_INPUT = path.resolve(__dirname, '../../gameplay-scripts/sato_hana_v1.json')
const DEFAULT_VOICE_ID = 'JTlYtJrcTzPC71hMLOxo'
const DEFAULT_MODEL_ID = 'eleven_v3'
const DEFAULT_OUTPUT_FORMAT = 'mp3_44100_128'
const DEFAULT_STABILITY = 0.5
const PUBLIC_AUDIO_ROOT = path.resolve(__dirname, '../../src/features/gameplay-director/audio')

function parseArgs(argv) {
  const args = {}
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (!arg.startsWith('--')) continue
    const [rawKey, inlineValue] = arg.slice(2).split('=', 2)
    const key = rawKey.replace(/-([a-z])/g, (_, character) => character.toUpperCase())
    if (inlineValue !== undefined) {
      args[key] = inlineValue
      continue
    }
    const next = argv[index + 1]
    if (!next || next.startsWith('--')) args[key] = true
    else {
      args[key] = next
      index += 1
    }
  }
  return args
}

function showHelp() {
  console.log(`
Generate ElevenLabs v3 audio for a Gameplay Director conversation.

Usage:
  npm run audio:chat-mock -- [options]

Options:
  --input <path>        Conversation JSON (default: gameplay-scripts/sato_hana_v1.json)
  --output-dir <path>   Override the webapp audio destination
  --only <ids>          Comma-separated message IDs to generate
  --limit <number>      Generate only the first N pending messages
  --concurrency <n>     Parallel API requests (default: 1)
  --voice-id <id>       Voice override (default: ${DEFAULT_VOICE_ID})
  --model-id <id>       Model override (default: ${DEFAULT_MODEL_ID})
  --stability <0..1>    Voice stability (default: ${DEFAULT_STABILITY})
  --output-format <id>  Audio format (default: ${DEFAULT_OUTPUT_FORMAT})
  --force               Regenerate files that already exist
  --yes                 Skip the typed confirmation
  --dry-run             Validate and list work without API calls or file writes
  --help                Show this help

Environment:
  ELEVENLABS_API_KEY is required for real generation.
`)
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function parsePositiveInteger(value, fallback) {
  const parsed = Number.parseInt(value, 10)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function parseStability(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    throw new Error('Stability must be a number between 0 and 1.')
  }
  return parsed
}

function validateMessageId(id, index) {
  if (typeof id !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) {
    throw new Error(`npc.message at event ${index + 1} needs a path-safe kebab-case id.`)
  }
}

function collectAudioLines(data) {
  if (!data || data.schemaVersion !== 1 || !Array.isArray(data.events)) {
    throw new Error('Input must be a schemaVersion 1 conversation with an events array.')
  }
  if (typeof data.id !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(data.id)) {
    throw new Error('Conversation id must be path-safe kebab-case.')
  }

  const ids = new Set()
  const lines = []
  data.events.forEach((event, index) => {
    if (event?.type !== 'npc.message') return
    validateMessageId(event.id, index)
    if (ids.has(event.id)) throw new Error(`Duplicate npc.message id: ${event.id}.`)
    ids.add(event.id)

    if (event.audio == null) return
    if (typeof event.audio.text !== 'string' || !event.audio.text.trim()) {
      throw new Error(`npc.message ${event.id} has audio without non-empty text.`)
    }
    lines.push({
      id: event.id,
      text: event.audio.text,
      language: event.audio.language || 'ja',
      eventIndex: index
    })
  })
  return { conversationId: data.id, lines }
}

function getExtension(outputFormat) {
  if (outputFormat.startsWith('mp3_')) return '.mp3'
  throw new Error(`Unsupported output format for webapp playback: ${outputFormat}. Use an mp3_* format.`)
}

function createPayload(line, options = {}) {
  return {
    text: line.text,
    model_id: options.modelId || DEFAULT_MODEL_ID,
    language_code: line.language,
    voice_settings: { stability: options.stability ?? DEFAULT_STABILITY }
  }
}

async function readResponseError(response) {
  const requestId = response.headers.get('x-request-id') || response.headers.get('request-id') || ''
  let body = ''
  try { body = await response.text() } catch (_) {}
  return [`${response.status} ${response.statusText}`.trim(), requestId && `request-id=${requestId}`, body.trim()].filter(Boolean).join(' | ')
}

async function generateAudio({ apiKey, voiceId, line, outputPath, modelId, outputFormat, stability, fetchImpl = fetch }) {
  const url = new URL(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voiceId)}`)
  url.searchParams.set('output_format', outputFormat)
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: {
      'xi-api-key': apiKey,
      'Content-Type': 'application/json',
      Accept: 'audio/*'
    },
    body: JSON.stringify(createPayload(line, { modelId, stability }))
  })
  if (!response.ok) throw new Error(await readResponseError(response))

  const temporaryPath = `${outputPath}.part-${process.pid}`
  try {
    const audio = Buffer.from(await response.arrayBuffer())
    if (!audio.length) throw new Error('ElevenLabs returned an empty audio response.')
    fs.writeFileSync(temporaryPath, audio)
    fs.renameSync(temporaryPath, outputPath)
  } finally {
    if (fs.existsSync(temporaryPath)) fs.unlinkSync(temporaryPath)
  }
}

function confirmGeneration(count, skipped, characters) {
  if (!process.stdin.isTTY) {
    throw new Error('Confirmation requires an interactive terminal. Re-run with --yes.')
  }
  const expected = `generate ${count}`
  const prompt = `\nGenerate ${count} file(s); skip ${skipped}; estimated characters: ${characters}.\nType "${expected}" to confirm: `
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  return new Promise((resolve, reject) => rl.question(prompt, answer => {
    rl.close()
    if (answer.trim() === expected) resolve()
    else reject(new Error('Generation cancelled.'))
  }))
}

async function runWithConcurrency(items, concurrency, worker) {
  let cursor = 0
  async function runWorker() {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      await worker(items[index], index)
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, runWorker))
}

function renderProgress(state) {
  if (!process.stdout.isTTY) return
  const width = 28
  const done = state.completed + state.failed
  const filled = state.total ? Math.round(width * done / state.total) : width
  const bar = `${'█'.repeat(filled)}${'░'.repeat(width - filled)}`
  process.stdout.write('\x1Bc')
  console.log('ElevenLabs gameplay audio')
  console.log(`[${bar}] ${done}/${state.total}`)
  console.log(`Done: ${state.completed}  Failed: ${state.failed}  Skipped: ${state.skipped}`)
  console.log(`Active: ${state.active.join(', ') || 'idle'}`)
  if (state.recent.length) console.log(`Recent: ${state.recent.slice(-4).join(', ')}`)
  if (state.errors.length) console.log(`Errors:\n${state.errors.slice(-3).join('\n')}`)
}

async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv)
  if (args.help) return showHelp()

  const inputPath = path.resolve(args.input || DEFAULT_INPUT)
  const data = readJson(inputPath)
  const { conversationId, lines: allLines } = collectAudioLines(data)
  const outputFormat = args.outputFormat || DEFAULT_OUTPUT_FORMAT
  const outputDir = path.resolve(args.outputDir || path.join(PUBLIC_AUDIO_ROOT, conversationId))
  const extension = getExtension(outputFormat)
  const only = args.only ? new Set(String(args.only).split(',').map(value => value.trim()).filter(Boolean)) : null
  const selectedLines = only ? allLines.filter(line => only.has(line.id)) : allLines
  if (only) {
    const missing = [...only].filter(id => !allLines.some(line => line.id === id))
    if (missing.length) throw new Error(`Unknown or non-audio message id(s): ${missing.join(', ')}.`)
  }
  if (!selectedLines.length) throw new Error('No audio messages matched the selection.')

  const force = Boolean(args.force)
  const dryRun = Boolean(args.dryRun)
  const jobs = selectedLines.map(line => ({ line, outputPath: path.join(outputDir, `${line.id}${extension}`) }))
  const skipped = jobs.filter(job => !force && fs.existsSync(job.outputPath))
  const pending = jobs.filter(job => force || !fs.existsSync(job.outputPath))
  const limit = args.limit === undefined ? null : parsePositiveInteger(args.limit, null)
  if (args.limit !== undefined && limit === null) throw new Error('--limit must be a positive integer.')
  const selectedJobs = limit ? pending.slice(0, limit) : pending
  const concurrency = parsePositiveInteger(args.concurrency, 1)
  const stability = parseStability(args.stability ?? DEFAULT_STABILITY)
  const voiceId = args.voiceId || DEFAULT_VOICE_ID
  const modelId = args.modelId || DEFAULT_MODEL_ID
  const apiKey = process.env.ELEVENLABS_API_KEY

  if (!dryRun && selectedJobs.length && !apiKey) {
    throw new Error('Missing ELEVENLABS_API_KEY in the shell environment.')
  }
  console.log(`Input: ${inputPath}`)
  console.log(`Output: ${outputDir}`)
  console.log(`Voice: ${voiceId}  Model: ${modelId}  Stability: ${stability}  Format: ${outputFormat}`)
  console.log(`Selected: ${selectedLines.length}  Pending: ${selectedJobs.length}  Existing: ${skipped.length}`)
  if (limit) console.log(`Limit: ${limit} (${Math.max(0, pending.length - selectedJobs.length)} pending left out)`)

  const characters = selectedJobs.reduce((sum, job) => sum + Array.from(job.line.text).length, 0)
  if (!dryRun && selectedJobs.length && !args.yes) await confirmGeneration(selectedJobs.length, skipped.length, characters)
  if (!dryRun && selectedJobs.length) fs.mkdirSync(outputDir, { recursive: true })

  const state = { total: selectedJobs.length, completed: 0, failed: 0, skipped: skipped.length, active: [], recent: [], errors: [] }
  const errors = []
  await runWithConcurrency(selectedJobs, dryRun ? 1 : concurrency, async (job, index) => {
    const prefix = `[${index + 1}/${selectedJobs.length}]`
    if (dryRun || !process.stdout.isTTY) {
      console.log(`${prefix} ${dryRun ? 'dry-run' : 'start'} ${job.line.id} -> ${job.outputPath}`)
      if (dryRun) console.log(`  ${job.line.text}`)
    } else {
      state.active.push(job.line.id)
      renderProgress(state)
    }
    if (dryRun) {
      state.completed += 1
      return
    }
    try {
      await generateAudio({ apiKey, voiceId, line: job.line, outputPath: job.outputPath, modelId, outputFormat, stability })
      state.completed += 1
      state.recent.push(`done ${job.line.id}`)
      if (!process.stdout.isTTY) console.log(`${prefix} done ${job.line.id}`)
    } catch (error) {
      state.failed += 1
      const detail = `${job.line.id}: ${error.message}`
      state.errors.push(detail)
      errors.push(detail)
      if (!process.stdout.isTTY) console.error(`${prefix} fail ${detail}`)
    } finally {
      state.active = state.active.filter(id => id !== job.line.id)
      renderProgress(state)
    }
  })

  console.log(`Done. Generated: ${dryRun ? 0 : state.completed}. Dry-run: ${dryRun ? state.completed : 0}. Skipped: ${skipped.length}. Failed: ${state.failed}.`)
  if (errors.length) throw new Error(`${errors.length} audio file(s) failed.`)
}

if (require.main === module) {
  main().catch(error => {
    console.error(error.message)
    process.exitCode = 1
  })
}

module.exports = {
  DEFAULT_MODEL_ID,
  DEFAULT_STABILITY,
  DEFAULT_VOICE_ID,
  collectAudioLines,
  createPayload,
  getExtension,
  parseArgs,
  parseStability
}
