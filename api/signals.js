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
    // 1. Fetch latest buy candidates
    const buyRes = await pool.query(`
      SELECT 
        s.symbol, 
        c.name, 
        c.sector, 
        s.score, 
        s.risk_level as "riskLevel", 
        s.suggested_weight_pct as "suggestedWeightPct", 
        s.reason
      FROM signals s
      JOIN companies c ON s.symbol = c.symbol
      WHERE s.signal_type = 'buy' 
        AND s.signal_date = (SELECT MAX(signal_date) FROM signals)
      ORDER BY s.score DESC
    `);

    // 2. Fetch latest sell candidates
    const sellRes = await pool.query(`
      SELECT symbol, reason
      FROM signals
      WHERE signal_type = 'sell' 
        AND signal_date = (SELECT MAX(signal_date) FROM signals)
    `);

    // 3. Get latest update metadata
    const metaRes = await pool.query(`
      SELECT MAX(signal_date) as "latestDate" FROM signals
    `);
    
    const latestDate = metaRes.rows[0]?.latestDate 
      ? new Date(metaRes.rows[0].latestDate).toISOString().split('T')[0] 
      : new Date().toISOString().split('T')[0];

    return res.status(200).json({
      generatedAt: latestDate,
      buy_candidates: buyRes.rows,
      sell_candidates: sellRes.rows,
      market_regime: 'Bullish' // Defaults to Bullish, can be updated via cron states
    });
  } catch (error) {
    console.error('API Error in /api/signals:', error);
    return res.status(500).json({ error: error.message });
  }
}
