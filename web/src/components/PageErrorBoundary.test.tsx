import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { PageErrorBoundary } from './PageErrorBoundary'

function BrokenPage(): never {
  throw new Error('sensitive diagnostic detail')
}

test('keeps a generic recovery screen when a page throws', () => {
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  render(<PageErrorBoundary><BrokenPage /></PageErrorBoundary>)

  expect(screen.getByText('当前页面暂时无法显示')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重新加载当前页面' })).toBeInTheDocument()
  expect(screen.queryByText('sensitive diagnostic detail')).not.toBeInTheDocument()
  consoleError.mockRestore()
})
