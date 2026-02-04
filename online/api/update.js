import { snapshot } from './_snapshot.js';

export default async function handler(req, res) {
  const token = req.query.token;
  if (!process.env.UPDATE_TOKEN || token !== process.env.UPDATE_TOKEN) {
    res.status(401).json({ error: 'Unauthorized' });
    return;
  }

  try {
    const host = req.headers.host;
    const response = await fetch(`https://${host}/api/data`);
    const data = await response.json();
    data.last_updated = new Date().toISOString().slice(0, 10);
    res.setHeader('Content-Type', 'application/json');
    res.status(200).json(data);
  } catch (_err) {
    const data = { ...snapshot, last_updated: new Date().toISOString().slice(0, 10) };
    res.setHeader('Content-Type', 'application/json');
    res.status(200).json(data);
  }
}
