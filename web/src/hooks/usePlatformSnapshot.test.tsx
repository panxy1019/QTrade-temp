import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PlatformSnapshot } from '../types'

const { loadPlatformSnapshotMock } = vi.hoisted(() => ({
  loadPlatformSnapshotMock: vi.fn(),
}))

vi.mock('../api/client', () => ({
  loadPlatformSnapshot: loadPlatformSnapshotMock,
}))

import { usePlatformSnapshot } from './usePlatformSnapshot'

function snapshot(failures: Record<string, string>): PlatformSnapshot {
  return { availability: { failures } } as PlatformSnapshot
}

afterEach(() => {
  vi.useRealTimers()
  loadPlatformSnapshotMock.mockReset()
})

describe('platform snapshot cold-start recovery', () => {
  it('retries one partial initial snapshot after five seconds', async () => {
    vi.useFakeTimers()
    loadPlatformSnapshotMock
      .mockResolvedValueOnce(snapshot({ minuteEvidence: '请求失败：503' }))
      .mockResolvedValueOnce(snapshot({}))
    const { result, unmount } = renderHook(() => usePlatformSnapshot(60_000))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(loadPlatformSnapshotMock).toHaveBeenCalledTimes(1)
    expect(result.current.data?.availability.failures).toEqual({
      minuteEvidence: '请求失败：503',
    })

    await act(async () => { await vi.advanceTimersByTimeAsync(4_999) })
    expect(loadPlatformSnapshotMock).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(loadPlatformSnapshotMock).toHaveBeenCalledTimes(2)
    expect(result.current.data?.availability.failures).toEqual({})
    unmount()
  })

  it('does not retry a complete initial snapshot', async () => {
    vi.useFakeTimers()
    loadPlatformSnapshotMock.mockResolvedValue(snapshot({}))
    const { unmount } = renderHook(() => usePlatformSnapshot(60_000))

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })

    expect(loadPlatformSnapshotMock).toHaveBeenCalledTimes(1)
    unmount()
  })
})
