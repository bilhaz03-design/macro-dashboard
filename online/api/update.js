import { buildData } from './_buildData.js';
import { snapshot } from './_snapshot.js';

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms)),
  ]);
}

export default async function handler(req, res) {
  const token = req.query.token;
  if (!process.env.UPDATE_TOKEN || token !== process.env.UPDATE_TOKEN) {
    res.status(401).json({ error: 'Unauthorized' });
    return;
  }

  try {
    // Rebuild data directly to avoid host-header based SSRF and stale-as-fresh responses.
    const data = await withTimeout(buildData(), 7500);
    res.setHeader('Content-Type', 'application/json');
    res.status(200).json(data);
  } catch (_err) {
    // Return stale snapshot with its original last_updated date and a 503 status
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('X-Served-From', 'snapshot');
    res.status(503).json(snapshot);
  }
}
