-- Synthetic custom staging layout; not an Oracle-delivered EBS control file.
LOAD DATA
INFILE 'correct.dat'
INTO TABLE XX_CHAR_INT
(
  CODE POSITION(1:2) CHAR(2),
  QUANTITY POSITION(3:6) INTEGER EXTERNAL(4)
)
