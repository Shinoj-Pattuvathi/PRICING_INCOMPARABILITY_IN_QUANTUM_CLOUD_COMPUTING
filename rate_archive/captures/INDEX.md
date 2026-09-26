# Rate-page captures

Captured 26 September 2026 (print-to-PDF from the live pages).
IMPORTANT: these captures document the tariff pages as retrievable at deposit
time. The execution-window constants were verified on 2026-08-16 and (device-
specific Azure constants) 2026-09-01, as recorded in ../index.csv; today's
captures corroborate every archived constant but are NOT the execution-day
snapshot.

| File | Source | Key contents | SHA-256 |
|---|---|---|---|
| aws_braket_pricing_2026-09-26.pdf | https://aws.amazon.com/braket/pricing/ | AWS Braket volume-meter rates: $0.30/task; per-shot $0.08 (IonQ Forte), $0.000425 (Rigetti Cepheus-1-108Q), $0.00145 (IQM Garnet) | cfb5ec0654f2fa07f9154ae48623dc38858106c8e5200065f1355eba908b2085 |
| ibm_quantum_pricing_2026-09-26.pdf | https://www.ibm.com/quantum (products and services page) | IBM Quantum pay-as-you-go time meter: $96 per QPU minute | 541f7de98e83dd176fc0974a1662a2756b23e2e0b27207c5cbe31b165223ed50 |
| microsoft_azure_quantum_provider_pricing_2026-09-26.pdf | https://learn.microsoft.com/azure/quantum/pricing | Azure Quantum IonQ device-specific AQT constants: Aria $0.000220/1q, $0.000975/2q, minima $12.4166 (mit-off)/$97.50 (mit-on); Forte $0.0001645/1q, $0.001121/2q, minima $25.7899 (mit-off)/$168.195 (mit-on) | da9d76b9f79ee82b0476be5929359683ec300341e031087ff2030045a618e32a |

Still to add: dated captures are not held for the IonQ direct-cloud page
(ionq.com/quantum-cloud; channel never executed) -- optional.
