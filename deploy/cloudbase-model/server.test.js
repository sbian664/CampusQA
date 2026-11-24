const assert = require('node:assert/strict')
const { once } = require('node:events')
const { Readable } = require('node:stream')
const { test } = require('node:test')
const { createServer, createCloudBaseCompletion, resolveCloudRunConfig } = require('./server')

const token = 'local-test-token'
const messages = [{ role: 'user', content: '你好' }]

async function start(t, complete, model = 'hy3') {
  const server = createServer({ token, model, complete })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => {
    server.close()
    server.closeAllConnections()
  })
  const url = `http://127.0.0.1:${server.address().port}/v1/chat/completions`
  return (body, authorization = `Bearer ${token}`, signal) => fetch(url, {
    method: 'POST',
    headers: { authorization, 'content-type': 'application/json' },
    body: typeof body === 'string' ? body : JSON.stringify(body),
    signal,
  })
}

test('rejects a local process configured with the target environment alone', () => {
  assert.throws(() => resolveCloudRunConfig({ CLOUDBASE_ENV_ID: 'activity-env' }), /CBR_ENV_ID/)
})

test('rejects deployment in an environment other than the configured activity environment', () => {
  assert.throws(() => resolveCloudRunConfig({
    CBR_ENV_ID: 'other-env', CLOUDBASE_ENV_ID: 'activity-env',
  }), /match/)
})

test('uses the activity environment and hy4 by default in CloudBase Run', () => {
  assert.deepEqual(resolveCloudRunConfig({
    CBR_ENV_ID: 'activity-env', CLOUDBASE_ENV_ID: 'activity-env', CAMPUSQA_BRIDGE_TOKEN: token,
  }), { env: 'activity-env', model: 'hy4', token })
})

test('accepts the matched CloudBase HTTP function runtime', () => {
  assert.deepEqual(resolveCloudRunConfig({
    TENCENTCLOUD_RUNENV: 'SCF', SCF_NAMESPACE: 'activity-env',
    CLOUDBASE_ENV_ID: 'activity-env', CAMPUSQA_BRIDGE_TOKEN: token,
  }), { env: 'activity-env', model: 'hy4', token })
})

test('hy4 disables thinking without dropping the RAG tools', async (t) => {
  const tools = [{ type: 'function', function: { name: 'search' } }]
  const request = await start(t, (data) => {
    assert.deepEqual(data.tools, tools)
    assert.deepEqual(data.thinking, { type: 'disabled' })
    return { choices: [{ message: { content: 'OK' } }] }
  }, 'hy4')
  const response = await request({ model: 'hy4', messages, tools })
  assert.equal(response.status, 200)
})

test('rejects callers without the backend token before contacting the model', async (t) => {
  const request = await start(t, () => assert.fail('called model without authorization'))
  assert.equal((await request({ model: 'hy3', messages }, 'Bearer invalid')).status, 401)
})

test('rejects unexpected models, bad JSON and oversized input', async (t) => {
  const request = await start(t, () => assert.fail('called model with invalid input'))
  assert.equal((await request({ model: 'wrong', messages })).status, 400)
  assert.equal((await request('{')).status, 400)
  assert.equal((await request('a'.repeat(1024 * 1024 + 1))).status, 413)
})

test('keeps tool calls and usage in the JSON response', async (t) => {
  const tools = [{ type: 'function', function: { name: 'search', parameters: { type: 'object' } } }]
  const toolCalls = [{ id: 'call_1', function: { name: 'search', arguments: '{"query":"课程"}' } }]
  const result = {
    choices: [{ message: { role: 'assistant', tool_calls: toolCalls }, finish_reason: 'tool_calls' }],
    usage: { prompt_tokens: 15, completion_tokens: 3, total_tokens: 18 },
  }
  const completion = { model: 'hy3', messages, tools, tool_choice: 'auto', max_tokens: 80, temperature: 0 }
  const request = await start(t, (data) => {
    assert.deepEqual(data, { ...completion, stream: false, thinking: { type: 'disabled' } })
    return result
  })
  const response = await request({ ...completion, base_url: 'ignored' })
  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), result)
})

test('preserves raw deltas, finish reason, usage and the SSE terminator', async (t) => {
  const first = 'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
  const last = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":9}}\n\ndata: [DONE]\n\n'
  const encoder = new TextEncoder()
  let upstream
  const request = await start(t, (data) => {
    assert.deepEqual(data.stream_options, { include_usage: true })
    return new ReadableStream({ start(controller) {
      upstream = controller
      controller.enqueue(encoder.encode(first))
    } })
  })
  const response = await request({ model: 'hy3', messages, stream: true })
  assert.match(response.headers.get('content-type'), /^text\/event-stream/)
  const reader = response.body.getReader()
  assert.equal(new TextDecoder().decode((await reader.read()).value), first)
  upstream.enqueue(encoder.encode(last))
  upstream.close()
  assert.equal(new TextDecoder().decode((await reader.read()).value), last)
  assert.equal((await reader.read()).done, true)
})

test('closing the client connection cancels its upstream stream', { timeout: 2000 }, async (t) => {
  const client = new AbortController()
  let cancelled
  const cancellation = new Promise((resolve) => { cancelled = resolve })
  const request = await start(t, () => new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(': connected\n\n')) },
    cancel: cancelled,
  }))
  await request({ model: 'hy3', messages, stream: true }, undefined, client.signal)
  client.abort()
  await cancellation
})

test('forwards node-fetch streams returned by the SDK in Cloud Run', async (t) => {
  const data = 'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
  const request = await start(t, () => Readable.from([data]))
  const response = await request({ model: 'hy3', messages, stream: true })
  assert.equal(response.status, 200)
  assert.equal(await response.text(), data)
})

test('disconnect cancels a request still waiting on the model', { timeout: 2000 }, async (t) => {
  const client = new AbortController()
  let notifyStarted, notifyCancelled
  const started = new Promise((resolve) => { notifyStarted = resolve })
  const cancelled = new Promise((resolve) => { notifyCancelled = resolve })
  const request = await start(t, (_, signal) => new Promise((resolve) => {
    notifyStarted()
    signal.addEventListener('abort', () => {
      notifyCancelled()
      resolve(null)
    }, { once: true })
  }))
  const result = assert.rejects(request({ model: 'hy3', messages }, undefined, client.signal), { name: 'AbortError' })
  await started
  client.abort()
  await result
  await cancelled
})

test('upstream failures never expose SDK errors to the client', async (t) => {
  const request = await start(t, () => { throw new Error('upstream secret detail') })
  const response = await request({ model: 'hy3', messages })
  assert.equal(response.status, 502)
  assert.doesNotMatch(await response.text(), /secret detail/)
})

test('sends a single model request through the CloudBase SDK', async () => {
  const payload = { model: 'hy3', messages, stream: false, response_format: { type: 'json_object' } }
  const result = { choices: [{ message: { content: '{}' }, finish_reason: 'stop' }] }
  const signal = new AbortController().signal
  const complete = createCloudBaseCompletion({
    ai: () => ({ aiBaseUrl: 'https://gateway.example.test/v1/ai' }),
    request: {
      async fetch(options) {
        assert.equal(options.url, 'https://gateway.example.test/v1/ai/cloudbase/chat/completions')
        assert.deepEqual(JSON.parse(options.body), payload)
        assert.equal(options.stream, false)
        assert.equal(options.signal, signal)
        return { data: result }
      },
    },
  })
  assert.equal(await complete(payload, signal), result)
})

test('does not treat an SDK gateway HTML error as a completed stream', async () => {
  const body = Readable.from(['<html>gateway error</html>'])
  const complete = createCloudBaseCompletion({
    ai: () => ({ aiBaseUrl: 'https://gateway.example.test/v1/ai' }),
    request: {
      async fetch() {
        return { data: body, header: new Headers({ 'content-type': 'text/html' }) }
      },
    },
  })
  await assert.rejects(complete({ model: 'hy3', messages, stream: true }))
  assert.equal(body.destroyed, true)
})
