/*
 * The firmware has no console or file-system path. These four newlib hooks
 * explicitly reject accidental I/O without routing it through USART3 or
 * pulling semihosting support into the image.
 */
int _close(int file)
{
    (void)file;
    return -1;
}

int _lseek(int file, int offset, int origin)
{
    (void)file;
    (void)offset;
    (void)origin;
    return -1;
}

int _read(int file, char *buffer, int length)
{
    (void)file;
    (void)buffer;
    (void)length;
    return -1;
}

int _write(int file, char *buffer, int length)
{
    (void)file;
    (void)buffer;
    (void)length;
    return -1;
}
