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

  const { symbol } = req.query;

  try {
    if (symbol) {
      // Get fundamentals for a single ticker
      const cleanSymbol = symbol.toUpperCase().endsWith('.IS') 
        ? symbol.toUpperCase() 
        : `${symbol.toUpperCase()}.IS`;
        
      const queryRes = await pool.query(`
        SELECT f.symbol, c.name, c.sector, f.report_date as "reportDate", f.metrics
        FROM fundamentals f
        JOIN companies c ON f.symbol = c.symbol
        WHERE f.symbol = $1
        ORDER BY f.report_date DESC
        LIMIT 1
      `, [cleanSymbol]);

      if (queryRes.rows.length === 0) {
        return res.status(404).json({ error: `Fundamentals not found for ticker: ${symbol}` });
      }

      return res.status(200).json(queryRes.rows[0]);
    } else {
      // Fetch latest snapshot metrics for all companies
      const queryRes = await pool.query(`
        SELECT DISTINCT ON (f.symbol) 
          f.symbol, 
          c.name, 
          c.sector, 
          f.report_date as "reportDate", 
          f.metrics
        FROM fundamentals f
        JOIN companies c ON f.symbol = c.symbol
        ORDER BY f.symbol, f.report_date DESC
      `);
      
      return res.status(200).json(queryRes.rows);
    }
  } catch (error) {
    console.error('API Error in /api/fundamentals:', error);
    return res.status(500).json({ error: error.message });
  }
}
