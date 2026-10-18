# Scenario 3 specification questions

The source CSV contains 63 mappings. Eight nonphysical or generated mappings do not require a DAT blank-value rule. The other 55 physical fields have no explicit mandatory flag. Every one of the 55 questions is **missing metadata**. No contradictory rule, mapping ambiguity, or unsupported requirement was found among these 55 approval findings. This classification does not establish that the source data is valid.

The user must set YES or NO where evidence exists, or explicitly acknowledge UNKNOWN. UNKNOWN remains a coverage limitation and blocks a fully validated DAT export. The application does not infer a blank-value rule from sample values.

| Source row | Field ID | Field name | Classification |
|---:|---|---|---|
| 18 | section-1-row-18 | RECORD_TYPE | Missing mandatory metadata |
| 19 | section-1-row-19 | BATCH_NAME | Missing mandatory metadata |
| 20 | section-1-row-20 | ITEM_NUMBER | Missing mandatory metadata |
| 21 | section-1-row-21 | OVERFLOW_INDICATOR | Missing mandatory metadata |
| 22 | section-1-row-22 | OVERFLOW_SEQUENCE | Missing mandatory metadata |
| 23 | section-1-row-23 | INVOICE1 | Missing mandatory metadata |
| 24 | section-1-row-24 | AMOUNT_APPLIED1 | Missing mandatory metadata |
| 25 | section-1-row-25 | CUSTOMER_REASON1 | Missing mandatory metadata |
| 26 | section-1-row-26 | CUSTOMER_NUMBER | Missing mandatory metadata |
| 27 | section-1-row-27 | ATTRIBUTE1 | Missing mandatory metadata |
| 28 | section-1-row-28 | ATTRIBUTE2 | Missing mandatory metadata |
| 29 | section-1-row-29 | ATTRIBUTE3 | Missing mandatory metadata |
| 30 | section-1-row-30 | CUSTOMER_REFERENCE1 | Missing mandatory metadata |
| 31 | section-1-row-31 | ORGANIZATION_NAME | Missing mandatory metadata |
| 32 | section-1-row-32 | CURRENCY_CODE | Missing mandatory metadata |
| 33 | section-1-row-33 | PAYMENT_DATE | Missing mandatory metadata |
| 34 | section-1-row-34 | GL_DATE | Missing mandatory metadata |
| 35 | section-1-row-35 | CM_TRX_NUMBER | Missing mandatory metadata |
| 36 | section-1-row-36 | CM_AMOUNT | Missing mandatory metadata |
| 37 | section-1-row-37 | CM_BATCH_SOURCE_NM | Missing mandatory metadata |
| 38 | section-1-row-38 | CM_REASON_CODE | Missing mandatory metadata |
| 39 | section-1-row-39 | CM_BILL_TO_LOCATION | Missing mandatory metadata |
| 40 | section-1-row-40 | CM_COMMENTS | Missing mandatory metadata |
| 41 | section-1-row-41 | DM_TRX_NUMBER | Missing mandatory metadata |
| 42 | section-1-row-42 | DM_AMOUNT | Missing mandatory metadata |
| 43 | section-1-row-43 | DM_BATCH_SOURCE_NM | Missing mandatory metadata |
| 44 | section-1-row-44 | DM_REASON_CODE | Missing mandatory metadata |
| 45 | section-1-row-45 | DM_TERM_NAME | Missing mandatory metadata |
| 46 | section-1-row-46 | DM_BILL_TO_LOCATION | Missing mandatory metadata |
| 47 | section-1-row-47 | DM_COMMENTS | Missing mandatory metadata |
| 48 | section-1-row-48 | ADJ_CUSTOMER_REFERENCE | Missing mandatory metadata |
| 49 | section-1-row-49 | ADJUSTMENT_ACTIVITY_NAME | Missing mandatory metadata |
| 50 | section-1-row-50 | ADJUSTMENT_AMOUNT | Missing mandatory metadata |
| 51 | section-1-row-51 | ADJ_GL_ACCOUNT_NUMBER | Missing mandatory metadata |
| 52 | section-1-row-52 | ADJUSTMENT_TYPE | Missing mandatory metadata |
| 53 | section-1-row-53 | ADJ_REASON_CODE | Missing mandatory metadata |
| 54 | section-1-row-54 | RECEIPT_NUMBER | Missing mandatory metadata |
| 55 | section-1-row-55 | OA_AMOUNT | Missing mandatory metadata |
| 56 | section-1-row-56 | OA_REASON_CODE | Missing mandatory metadata |
| 57 | section-1-row-57 | CB_TRX_NUMBER | Missing mandatory metadata |
| 58 | section-1-row-58 | CB_AMOUNT | Missing mandatory metadata |
| 59 | section-1-row-59 | CB_REASON_CODE | Missing mandatory metadata |
| 60 | section-1-row-60 | TRANSACTION_MEMO_LINE | Missing mandatory metadata |
| 61 | section-1-row-61 | PROCESS | Missing mandatory metadata |
| 62 | section-1-row-62 | PROCESS_TYPE | Missing mandatory metadata |
| 63 | section-1-row-63 | ATTRIBUTE6 | Missing mandatory metadata |
| 64 | section-1-row-64 | ATTRIBUTE15 | Missing mandatory metadata |
| 65 | section-1-row-65 | GLOBAL_ATTRIBUTE1 | Missing mandatory metadata |
| 66 | section-1-row-66 | ATTRIBUTE12 | Missing mandatory metadata |
| 67 | section-1-row-67 | SALES_ORDER | Missing mandatory metadata |
| 68 | section-1-row-68 | ATTRIBUTE10 | Missing mandatory metadata |
| 69 | section-1-row-69 | ATTRIBUTE11 | Missing mandatory metadata |
| 70 | section-1-row-70 | CM_TERM_NAME | Missing mandatory metadata |
| 71 | section-1-row-71 | CM_TRANSACTION_TYPE | Missing mandatory metadata |
| 72 | section-1-row-72 | DM_TRANSACTION_TYPE | Missing mandatory metadata |
