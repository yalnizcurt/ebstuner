-- Synthetic custom staging layout; not an Oracle-delivered EBS control file.
LOAD DATA INTO TABLE XX_SQL (CODE POSITION(1:2) CHAR(2), AMOUNT POSITION(3:10) CHAR(8) "TO_NUMBER(:AMOUNT)/100")