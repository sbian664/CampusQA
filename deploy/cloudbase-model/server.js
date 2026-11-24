const http = require('node:http')
const { timingSafeEqual } = require('node:crypto')
const { Readable } = require('node:stream')
const { pipeline } = require('node:stream/promises')

const MAX_REQUEST_BYTES = 1024 * 1024
const COMPLETION_FIELDS = ['max_tokens', 'temperature', 'tools', 'tool_choice', 'response_format']

function sendJson(response, status, data, headers = {}) {
  response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', ...headers })
  response.end(JSON.stringify(data))
}

function toNodeStream(body) {
  if (body instanceof Readable) return body
  if (typeof body?.getReader === 'function') return Readable.fromWeb(body)
  return null
}

async function readPayload(request, response, model) {
  const chunks = []
  let bytes = 0
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    bytes += chunk.length
    if (bytes > MAX_REQUEST_BYTES) {
      sendJson(response, 413, { error: 'Request exceeds 1 MiB' }, { connection: 'close' })
      return null
    }
    chunks.push(chunk)
  }

  let input
  try {
    input = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
  }
  if (input?.model !== model || !Array.isArray(input.messages) || input.messages.length === 0) {
    sendJson(response, 400, { error: 'Expected the configured model and a nonempty messages array' })
    return null
  }

  const stream = input.stream === true
  const payload = { model, messages: input.messages, stream }
  for (const field of COMPLETION_FIELDS) {
    if (Object.hasOwn(input, field)) payload[field] = input[field]
  }
  // CampusQA's tool history contains the tool messages and final answer only.
  if (['hy3', 'hy4', 'hy4-flash'].includes(model)) payload.thinking = { type: 'disabled' }
  if (stream) payload.stream_options = { include_usage: true }
  return payload
}

function createServer({ token, model, complete, pathPrefix = '' }) {
  if (!token || !model) throw new Error('CAMPUSQA_BRIDGE_TOKEN and model are required')
  const expectedAuth = Buffer.from(`Bearer ${token}`)

  return http.createServer(async (request, response) => {
    const route = pathPrefix && request.url.startsWith(pathPrefix + '/')
      ? request.url.slice(pathPrefix.length)
      : request.url
    if (request.method === 'GET' && route === '/healthz') {
      sendJson(response, 200, { status: 'ok' })
      return
    }
    if (request.method !== 'POST' || route !== '/v1/chat/completions') {
      sendJson(response, 404, { error: 'Not found' })
      return
    }
    const auth = Buffer.from(request.headers.authorization || '')
    if (auth.length !== expectedAuth.length || !timingSafeEqual(auth, expectedAuth)) {
      sendJson(response, 401, { error: 'Unauthorized' })
      return
    }

    try {
      const payload = await readPayload(request, response, model)
      if (!payload || response.destroyed) return

      const controller = new AbortController()
      response.once('close', () => {
        if (!response.writableFinished) controller.abort()
      })
      const result = await complete(payload, controller.signal)
      if (payload.stream) {
        const stream = toNodeStream(result)
        if (!stream) throw new Error('Expected a model stream')
        if (response.destroyed) {
          stream.destroy()
          return
        }
        response.writeHead(200, {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache, no-transform',
          'x-accel-buffering': 'no',
        })
        await pipeline(stream, response)
      } else if (!response.destroyed) {
        if (!result?.choices?.[0]?.message) throw new Error('Expected a model completion')
        sendJson(response, 200, result)
      }
    } catch {
      if (response.destroyed) return
      if (response.headersSent) {
        response.destroy()
      } else {
        sendJson(response, 502, { error: 'CloudBase model request failed' })
      }
    }
  })
}

function createCloudBaseCompletion(app) {
  const ai = app.ai()
  // The SDK request transport accepts an abort signal and returns raw model output.
  return async (payload, signal) => {
    const headers = { 'Content-Type': 'application/json' }
    if (payload.stream) headers.Accept = 'text/event-stream'
    const { data, header, statusCode } = await app.request.fetch({
      url: `${ai.aiBaseUrl}/cloudbase/chat/completions`,
      method: 'post',
      headers,
      body: JSON.stringify(payload),
      stream: payload.stream,
      signal,
    })
    const result = await data
    const mediaType = header?.get('content-type')?.split(';', 1)[0].trim().toLowerCase()
    if (statusCode >= 400 || (payload.stream && mediaType !== 'text/event-stream')) {
      toNodeStream(result)?.destroy()
      throw new Error('Unexpected CloudBase model response')
    }
    return result
  }
}

function resolveCloudRunConfig(environment) {
  const functionEnv = environment.TENCENTCLOUD_RUNENV === 'SCF' && environment.SCF_NAMESPACE?.trim()
  const env = environment.CBR_ENV_ID?.trim() || functionEnv
  if (!env) throw new Error('CloudBase runtime must supply CBR_ENV_ID or SCF_NAMESPACE')
  if (environment.CLOUDBASE_ENV_ID?.trim() !== env) {
    throw new Error('CLOUDBASE_ENV_ID must match the CloudBase runtime environment')
  }
  const token = environment.CAMPUSQA_BRIDGE_TOKEN?.trim()
  if (!token) throw new Error('CAMPUSQA_BRIDGE_TOKEN is required')
  const model = environment.CLOUDBASE_MODEL?.trim() || 'hy4'
  return { env, model, token }
}

if (require.main === module) {
  const { env, model, token } = resolveCloudRunConfig(process.env)
  const cloudbase = require('@cloudbase/node-sdk')
  const app = cloudbase.init({ env, timeout: 60_000 })
  const server = createServer({
    token, model, complete: createCloudBaseCompletion(app),
    pathPrefix: process.env.CAMPUSQA_BRIDGE_PATH || '',
  })
  server.listen(Number(process.env.PORT || 8080), '0.0.0.0')
}

module.exports = { createServer, createCloudBaseCompletion, resolveCloudRunConfig }
