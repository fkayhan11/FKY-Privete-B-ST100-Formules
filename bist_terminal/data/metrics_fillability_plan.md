# Metrik Doldurulabilirlik Planı

## Grup Özeti

- Doğal N/A (kaynak/model gereği): 2 metrik
- Sektör koşullu N/A (özellikle banka/sigorta): 3 metrik
- Doldurulabilir: 33 metrik

## Doğal N/A

- `forwardPE` (boş 100/100, coverage %0.0): KAP tarafında ileriye dönük tahmin verisi yok; bu alan modelde üretilmiyor.
- `fxNetPositionRatio` (boş 100/100, coverage %0.0): KAP’ta net döviz pozisyon kalemi standart şekilde ayrışmadığı için oran boş.

## Sektör Koşullu

- `debtMaturityRatio` (boş 13/100, coverage %87.0): Kısa vadeli borç kalemi boş olduğu için vade dağılımı oranı hesaplanamadı.
- `netDebtToEbitda` (boş 12/100, coverage %88.0): FAVÖK boş/0 olduğu için NetBorç/FAVÖK hesaplanamadı.
- `interestCoverage` (boş 12/100, coverage %88.0): Finansman gideri boş/0 olduğu için faiz karşılama hesaplanamadı.

## Doldurulabilir (Öncelik Sırası)

### Faz-1 (Yüksek Etki)

- `pegRatio`: boş 41/100
- `dividendYield`: boş 28/100
- `annualDividendPerShare`: boş 28/100
- `lastDividendPerShare`: boş 28/100
- `lastDividendDateMs`: boş 28/100
- `dividendPayoutPct`: boş 28/100
- `paidYears3y`: boş 28/100
- `regularityScore`: boş 28/100
- `eventCount`: boş 28/100
- Eylem: KAP temettü parser kapsamını genişlet + PEG için UI'da N/A neden etiketi (negatif büyüme).

### Faz-2 (Orta Etki)

- `ebitda`: boş 12/100
- `currentBorrowings`: boş 11/100
- `inventories`: boş 11/100
- `nonCurrentBorrowings`: boş 10/100
- `depreciationAmortization`: boş 10/100
- `interestExpense`: boş 8/100
- `cfo`: boş 7/100
- `capex`: boş 3/100
- Eylem: KAP taxonomy alias listesini genişlet (banka/finans için ayrı mapping).

### Faz-3 (Tamamlama)

- `operatingMargins`: boş 10/100
- `freeCashflow`: boş 10/100
- `currentRatio`: boş 9/100
- `quickRatio`: boş 9/100
- `currentAssets`: boş 9/100
- `currentLiabilities`: boş 9/100
- `grossMargins`: boş 8/100
- `cfoToNetIncome`: boş 7/100
- `revenueGrowth`: boş 6/100
- `profitMargins`: boş 6/100
- `priceToSalesTrailing12Months`: boş 6/100
- `assetTurnover`: boş 6/100
- `profitabilityStability`: boş 6/100
- `growthStability`: boş 6/100
- `operatingProfit`: boş 4/100
- `grossProfit`: boş 2/100
- Eylem: Baz kalem doldukça türetilen oranlar otomatik dolacak; ek manuel fallback yok.

## Kabul Kriteri

- Faz-1 sonrası temettü bloğu coverage: %72 -> hedef >= %85
- Faz-2 sonrası borç/dayanıklılık bloğu kritik alanları: hedef >= %95 (banka/finans hariç)
- Doğal N/A alanları UI'da "N/A (model gereği)" etiketiyle göster