export function isPublicResearchNode(hostname = window.location.hostname) {
  return !['127.0.0.1', 'localhost', '::1'].includes(hostname)
}
