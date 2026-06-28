import pkg from 'pg';
const { Pool } = pkg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

export default async function handler(req, res) {
  // CORS Headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const queryRes = await pool.query(`
      SELECT 
        p.symbol, 
        c.name, 
        c.sector, 
        p.entry_price as "entryPrice", 
        p.highest_price as "highestPrice", 
        p.last_price as "lastPrice", 
        p.trailing_stop_price as "trailingStopPrice", 
        p.z_score as "zScore", 
        p.atr14, 
        p.entry_signal_at as "entrySignalAt", 
        p.last_updated_at as "lastUpdatedAt"
      FROM scanner_positions p
      JOIN companies c ON p.symbol = c.symbol
      ORDER BY p.entry_signal_at DESC
    `);
    
    return res.status(200).json({
      positions: queryRes.rows
    });
  } catch (error) {
    console.error('API Error in /api/positions:', error);
    return res.status(500).json({ error: error.message });
  }
}
