import { buildData } from './_buildData.js';
import { snapshot } from './_snapshot.js';

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ms)),
  ]);
}

export default async function handler(req, res) {
  try {
    const data = await withTimeout(buildData(), 7500);
    res.setHeader('Content-Type', 'application/json');
    res.status(200).json(data);
  } catch (err) {
    res.setHeader('Content-Type', 'application/json');
    res.status(200).json(snapshot);
  }
}
