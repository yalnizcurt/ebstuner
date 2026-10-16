-- Synthetic custom staging layout; not an Oracle-delivered EBS control file.
LOAD DATA INFILE 'correct.dat' INTO TABLE XX_BINARY (CODE POSITION(1:2) CHAR(2), QUANTITY POSITION(3:6) INTEGER(4))